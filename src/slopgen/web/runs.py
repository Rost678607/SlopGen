"""Runs that outlive the page that started them.

The terminal UI and a run are the same process and the same attention: start one and
you watch it, because there is nowhere else to be. A browser is not like that — the
operator starts a run, goes to mark up a picture, comes back an hour later — so a run
here is a THING with a name, not a screen you are standing in front of.

Three consequences, and they are the whole of this module.

**A run keeps going when nobody is looking.** `Orchestrator.run` is blocking, so each
run gets a thread out of a small pool. The pool is also the queue: past
`web.max_parallel` a run simply waits its turn and says so.

**Coming back must show what was missed.** Every event is kept in a ring buffer as
well as broadcast, and a subscriber is handed the buffer before the live stream. That
is what makes closing the tab harmless rather than merely survivable.

**Stopping is between stages, never inside one.** A stage writes its own output; torn
in half it would leave that half on disk with nothing recording that it is half.
Between stages the checkpoint is current, so a stop is indistinguishable from a crash
the resume already survives (see `Orchestrator.should_stop`).

One thing is serialised beyond the pool: two runs of the same WORLD. Both would write
into `configs/fandoms/<name>/` — the canon sheet after a lore change, a delivered
frame card — and `ConfigStore` is one object per process that both would mutate. They
queue against each other however wide the pool is.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigStore, RunParams
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.context import AppContext
from ..pipeline.orchestrator import Orchestrator

log = logging.getLogger(__name__)

# How many events one run remembers for a page that comes back later. A long fandom
# run emits a few dozen; a batch of ten videos a few hundred. Keeping the lot costs
# nothing and means the buffer is never the reason something is missing.
BACKLOG = 2000

Status = str  # queued | running | paused | review | done | failed | stopped


@dataclass
class Event:
    seq: int
    at: float
    video: int
    stage: str
    status: str
    message: str

    def as_dict(self) -> dict:
        return {"seq": self.seq, "at": self.at, "video": self.video,
                "stage": self.stage, "status": self.status, "message": self.message}


@dataclass
class Run:
    id: str
    title: str
    params: RunParams
    status: Status = "queued"
    message: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    run_dir: Path | None = None
    resume_dir: Path | None = None
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=BACKLOG))
    progress: tuple[str, int, int] | None = None
    _seq: int = 0
    _stop: bool = False
    _subs: set[asyncio.Queue] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "message": self.message, "started_at": self.started_at,
            "finished_at": self.finished_at,
            "run_dir": str(self.run_dir) if self.run_dir else "",
            "mode": self.params.mode, "fandom": self.params.fandom,
            "count": self.params.count,
            "progress": {"unit": self.progress[0], "done": self.progress[1],
                         "total": self.progress[2]} if self.progress else None,
            "events": len(self.events),
            # what the run was asked for, so a caller can check the settings it sent
            # without waiting for a checkpoint to appear on disk. The ad-hoc objects
            # are left out: they are whole configs, not settings, and one of them is a
            # generator chain that would double the size of every row in the list.
            "params": self.params.model_dump(mode="json",
                                             exclude={"manual_cast", "manual_visuals",
                                                      "manual_ad", "manual_orchestration"}),
        }


class Supervisor:
    """Every run this server knows about, and the pool they take turns in."""

    def __init__(self, store: ConfigStore, max_parallel: int = 2):
        self.store = store
        self.runs: dict[str, Run] = {}
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_parallel),
                                        thread_name_prefix="slopgen-run")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        # one lock per world; see the module docstring
        self._world_locks: dict[str, threading.Lock] = {}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the loop that subscribers live on. Events arrive on worker
        threads and have to be handed over rather than pushed."""
        self._loop = loop

    # -- starting ----------------------------------------------------------

    def start(self, params: RunParams, title: str = "") -> Run:
        run = Run(id=uuid.uuid4().hex[:12], title=title or _title(params), params=params)
        self._register(run)
        self._pool.submit(self._work, run, None)
        return run

    def resume(self, run_dir: Path, title: str = "") -> Run:
        """Pick a parked run back up.

        A run is its FOLDER, not the object this server happens to be holding, so
        resuming reuses the entry that already points there rather than adding a
        second one. Two rows for one checkpoint is not a display quirk — the operator
        has to guess which of them is the real one, and stopping the wrong one does
        nothing while looking like it did something."""
        cp = Checkpoint.load(run_dir)
        with self._lock:
            run = next((r for r in self.runs.values() if r.run_dir == run_dir), None)
            if run is not None and run.status in ("running", "queued"):
                return run  # already going; resuming again would run it twice
        if run is None:
            run = Run(id=uuid.uuid4().hex[:12], title=title or run_dir.name,
                      params=cp.params, resume_dir=run_dir, run_dir=run_dir)
            with self._lock:
                self.runs[run.id] = run
        else:
            run.params = cp.params
            run._stop = False
            run.message = ""
            run.finished_at = 0.0
        run.status = "queued"
        self._emit(run, -1, "run", "queued", "resuming")
        self._pool.submit(self._work, run, run_dir)
        return run

    def adopt(self, run_dir: Path) -> Run | None:
        """Take an existing run on disk into this server, without starting anything.

        Runs outlive the process that made them — that is the whole point of a
        checkpoint — but the supervisor is memory, so a restart used to orphan every
        parked run: the work was all still there and there was no way to reach it. This
        reads what the checkpoint says and puts the run back on the list, settled, so
        it can be reviewed, asked about and resumed like any other."""
        cp_path = run_dir / "checkpoint.json"
        if not cp_path.is_file():
            return None
        with self._lock:
            for r in self.runs.values():
                if r.run_dir == run_dir:
                    return r
        try:
            cp = Checkpoint.load(run_dir)
        except Exception:
            return None
        states = [cp.status(i) for i in range(cp.params.count)]
        status = next((s for s in ("failed", "review", "paused") if s in states),
                      "done" if states and all(s == "done" for s in states) else "stopped")
        run = Run(id=uuid.uuid4().hex[:12], title=run_dir.name, params=cp.params,
                  status=status, run_dir=run_dir, resume_dir=run_dir,
                  message="js.found-on-disk", finished_at=cp_path.stat().st_mtime)
        with self._lock:
            self.runs[run.id] = run
        return run

    def adopt_all(self, output: Path, limit: int = 40) -> int:
        """Every run under the output folder, newest first. Bounded on purpose: after a
        few hundred videos the list is history, not a work queue."""
        if not output.is_dir():
            return 0
        dirs = sorted((p for p in output.rglob("checkpoint.json")),
                      key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
        return sum(1 for p in dirs if self.adopt(p.parent) is not None)

    def _register(self, run: Run) -> None:
        with self._lock:
            self.runs[run.id] = run
        self._emit(run, -1, "run", "queued", "waiting for a free slot")

    # -- stopping ----------------------------------------------------------

    def stop(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        if run is None or run.status in ("done", "failed", "stopped"):
            return False
        if run._stop:
            return True  # already asked; saying so again helps nobody
        run._stop = True
        if run.status == "queued":  # never started; nothing to unwind
            run.status = "stopped"
            run.finished_at = time.time()
            self._emit(run, -1, "run", "stopped", "cancelled before it began")
        else:
            self._emit(run, -1, "run", "stopping", "will stop after the current stage")
        return True

    # -- the worker --------------------------------------------------------

    def _world_lock(self, name: str) -> threading.Lock:
        with self._lock:
            return self._world_locks.setdefault(name, threading.Lock())

    def _work(self, run: Run, resume_dir: Path | None) -> None:
        if run._stop:
            return
        world = run.params.fandom or ""
        lock = self._world_lock(world) if world else None
        if lock is not None and not lock.acquire(blocking=False):
            self._emit(run, -1, "run", "queued", f"waiting for another run of «{world}»")
            lock.acquire()
        try:
            run.status = "running"
            run.started_at = time.time()
            self._emit(run, -1, "run", "start", run.title)
            ctx = AppContext(store=self.store, params=run.params,
                             on_progress=lambda u, d, t: self._progress(run, u, d, t))
            orch = Orchestrator(
                ctx,
                on_event=lambda i, st, status, msg: self._emit(run, i, st, status, msg),
                should_stop=lambda: run._stop,
            )
            orch.run(resume_dir=resume_dir)
            run.run_dir = orch.run_dir
            run.status = "stopped" if run._stop else _final_status(orch, run)
            self._emit(run, -1, "run", run.status, str(orch.run_dir or ""))
        except Exception as e:  # a failed run is a state, not a crash of the server
            run.status = "failed"
            run.message = f"{type(e).__name__}: {e}"
            log.exception("run %s failed", run.id)
            self._emit(run, -1, "run", "failed", run.message)
        finally:
            run.finished_at = time.time()
            if lock is not None:
                lock.release()

    def _progress(self, run: Run, unit: str, done: int, total: int) -> None:
        run.progress = (unit, done, total)

    def _emit(self, run: Run, video: int, stage: str, status: str, message: str) -> None:
        run._seq += 1
        ev = Event(seq=run._seq, at=time.time(), video=video, stage=stage,
                   status=status, message=message or "")
        run.events.append(ev)
        if status in ("paused", "review"):
            run.status, run.message = status, ev.message
        if self._loop is None:
            return
        for q in list(run._subs):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, ev)
            except RuntimeError:  # the loop went away under us; the buffer still has it
                pass

    # -- watching ----------------------------------------------------------

    def subscribe(self, run: Run) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        run._subs.add(q)
        return q

    def unsubscribe(self, run: Run, q: asyncio.Queue) -> None:
        run._subs.discard(q)

    def backlog(self, run: Run, after: int = 0) -> list[Event]:
        """Everything that happened before this page arrived. Handing it over IS the
        feature: without it, coming back to a run shows an empty log and a spinner."""
        return [e for e in run.events if e.seq > after]

    def shutdown(self) -> None:
        for run in self.runs.values():
            run._stop = True
        self._pool.shutdown(wait=False, cancel_futures=True)


def _final_status(orch: Orchestrator, run: Run) -> Status:
    """What a finished `run()` actually left behind. The orchestrator returns jobs
    rather than a verdict, so the checkpoint is what knows: a run that parked waiting
    for pictures is neither done nor failed, and calling it done would hide the one
    thing the operator has to act on."""
    if run.status in ("paused", "review"):
        return run.status
    if orch.run_dir is None:
        return "done"
    try:
        cp = Checkpoint.load(orch.run_dir)
    except Exception:
        return "done"
    states = {cp.status(i) for i in range(run.params.count)}
    for bad in ("failed", "paused", "review"):
        if bad in states:
            return bad
    return "done"


def _title(p: RunParams) -> str:
    bits = [p.mode]
    if p.fandom:
        bits.append(p.fandom)
    if p.count > 1:
        bits.append(f"×{p.count}")
    return " · ".join(bits)
