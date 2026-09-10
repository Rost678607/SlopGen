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

**A loop is a producer of ordinary runs.** `start_loop` puts `pipeline.loop` on a thread
of its own — not a pool slot, which it would hold for hours while making nothing — and
each iteration goes through `start` like anything else. So a looped video is reviewable,
resumable and stoppable by everything already written here, and the loop is the only new
thing: a plan on disk that can be edited between videos (see `pipeline/loop.py`).

One thing is serialised beyond the pool: two runs of the same WORLD. Both would write
into `configs/fandoms/<name>/` — the canon sheet after a lore change, a delivered
frame card — and `ConfigStore` is one object per process that both would mutate. They
queue against each other however wide the pool is.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigStore, RunParams
from ..pipeline import manual
from ..pipeline.checkpoint import Checkpoint, outcome
from ..pipeline.context import AppContext
from ..pipeline.loop import (LaunchResult, LoopFile, LoopRunner, QueueItem,
                             loop_dir_name)
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
    # How far through its own loop a stage is, when it is one that counts (voiced
    # lines, generated clips, assembled scenes). Zeroes everywhere else, which is what
    # `PROGRESS_STAGE` below is for: a reader tells a tally from a message by the
    # stage name, never by guessing at the numbers.
    done: int = 0
    total: int = 0

    def as_dict(self) -> dict:
        return {"seq": self.seq, "at": self.at, "video": self.video,
                "stage": self.stage, "status": self.status, "message": self.message,
                "done": self.done, "total": self.total}


# The stage name a progress tick carries instead of a real stage. It is not a stage and
# never appears in a checkpoint: it exists so one stream can carry both the log and the
# bar, and so a reader can drop it out of the log without pattern-matching on words.
PROGRESS_STAGE = "progress"


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
    # the loop that started this run, if one did. A looped video is an ordinary run in
    # every other respect — it parks, it is reviewed, it is resumed the same way — and
    # this is only so the page can say which loop it came out of.
    loop_id: str = ""
    events: deque[Event] = field(default_factory=lambda: deque(maxlen=BACKLOG))
    progress: tuple[str, int, int] | None = None
    # The stage this run is inside right now, and which video it is on. The terminal
    # has always shown both, over a bar; the browser had the tally on the wire and
    # nothing to hang it on, because nothing recorded WHAT was counting.
    stage: str = ""
    stage_video: int = -1
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
            "count": self.params.count, "loop_id": self.loop_id,
            "stage": self.stage, "stage_video": self.stage_video,
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


@dataclass
class Loop:
    """A loop and the videos it has made.

    Almost nothing is kept here: the plan on disk is the loop, and it is re-read on
    every request rather than cached, because the point of the file is that anything may
    have edited it — this server, a terminal, the operator with an editor open."""

    id: str
    title: str
    file: LoopFile
    params: RunParams
    started_at: float = 0.0
    finished_at: float = 0.0
    run_ids: list[str] = field(default_factory=list)
    _stop: bool = False

    def as_dict(self) -> dict:
        plan = self.file.read()
        return {
            "id": self.id, "title": self.title, "dir": str(self.file.dir),
            "status": plan.status, "note": plan.note, "live": plan.live,
            # Told to stop, and not stopped YET. The two are a real state and not a
            # transient: `loop stop` deliberately never tears the video being made in
            # half (see the README), so a loop can sit here for the length of a whole
            # video. The flag was written to the file and shown to nobody, so the card
            # went on saying `running` with a stop button that had already been pressed
            # — which reads as a button that does not work.
            "stopping": bool(plan.stop and plan.live),
            # and WHICH video is holding it, so the one thing that ends it now is one
            # press away instead of a hunt through the runs tab for a title that does
            # not say it belongs to a loop
            "at_run": self.run_ids[-1] if self.run_ids and plan.live else "",
            "mode": plan.params.mode, "fandom": plan.params.fandom,
            "source": plan.source, "limit": plan.limit, "ahead": plan.ahead,
            # the queue as ENTRIES, not sentences: each one carries its own settings and
            # the id every edit addresses it by (see pipeline/loop.QueueItem)
            "topics": [i.model_dump(mode="json") for i in plan.topics],
            "breakpoints": plan.breakpoints, "on_park": plan.on_park,
            "started": plan.started, "made": plan.made,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "iterations": [it.model_dump(mode="json") for it in plan.iterations],
            "run_ids": list(self.run_ids),
            # The settings every next video is built from — sent whole, because the page
            # edits them in the same form that starts a run and a form cannot be filled
            # from a summary. The two ad-hoc configs a form can express are sent beside
            # them as what the form actually shows: a cast is its names, a picture
            # source is the one generator its chain names.
            "params": plan.params.model_dump(mode="json",
                                             exclude={"manual_cast", "manual_visuals",
                                                      "manual_ad", "manual_orchestration"}),
            "cast": [c.name for c in plan.params.manual_cast],
            "picture_source": (plan.params.manual_orchestration.stages[0].model
                               if plan.params.manual_orchestration
                               and plan.params.manual_orchestration.stages else ""),
        }


def _mtime(path: Path) -> float:
    """When this file last changed, or 0.0 when it is not there — a missing manifest is
    a perfectly ordinary state and a stamp made of it must not throw."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def parked(run: Run) -> dict:
    """What this run is actually waiting for, read off its own folder.

    A status is not enough to decide what may be done with a run. "stopped" says
    nothing about whether it was sitting on a breakpoint when it stopped, and
    "paused" says nothing about how many pictures are still owed. Offering every
    action to every settled run was the first version, and it meant most buttons did
    nothing when pressed — which reads as a broken page rather than as an answer.

    These files are small and this is cached against their mtimes, so a list of forty
    runs costs a few stat calls per run and nothing else. The MANIFESTS are part of the
    stamp and not only the checkpoint, because they are the half that moves on its own:
    a picture handed over from the page or attached in the gather screen changes what is
    owed without the checkpoint being written at all, and a count cached against the
    checkpoint alone went on telling the operator five pictures were missing seconds
    after they had delivered the fifth.

    It lives here rather than in the browser's routes because the chat asks the same
    question: a bot that says "parked" and nothing else is exactly the broken page
    again, in fewer pixels."""
    if run.run_dir is None:
        return {"review_stage": "", "asks": 0, "video": False}
    cp_file = run.run_dir / "checkpoint.json"
    try:
        works = sorted(p for p in run.run_dir.iterdir() if p.is_dir())
    except OSError:
        return {"review_stage": "", "asks": 0, "video": False}
    stamp = tuple(_mtime(f) for f in
                  [cp_file, *(manual.manifest_path(w) for w in works)])
    cached = getattr(run, "_parked", None)
    if cached and cached[0] == stamp:
        return cached[1]
    info = {"review_stage": "", "asks": 0, "video": False}
    try:
        cp = Checkpoint.load(run.run_dir)
        for i in range(run.params.count):
            info["review_stage"] = info["review_stage"] or cp.review_stage(i)
    except Exception:
        pass
    for work in works:
        mp = manual.manifest_path(work)
        if mp.is_file():
            try:
                mf = manual.ManualManifest.model_validate_json(mp.read_text(encoding="utf-8"))
                info["asks"] += sum(1 for sh in mf.shots if sh.status != "delivered")
            except Exception:
                pass
        info["video"] = info["video"] or any(work.glob("*.mp4"))
    run._parked = (stamp, info)
    return info


class Supervisor:
    """Every run this server knows about, and the pool they take turns in."""

    def __init__(self, store: ConfigStore, max_parallel: int = 2):
        self.store = store
        self.runs: dict[str, Run] = {}
        self.loops: dict[str, Loop] = {}
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

    def start(self, params: RunParams, title: str = "", loop_id: str = "") -> Run:
        run = Run(id=uuid.uuid4().hex[:12], title=title or _title(params), params=params,
                  loop_id=loop_id)
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

    def adopt_loop(self, loop_dir: Path) -> "Loop | None":
        """Take an existing loop's plan on disk into this server, without running it.

        The exact counterpart of :meth:`adopt` — and it was missing, which cost a
        queue. A loop is a plan on disk and a thread in memory; only the thread dies
        with the process, but nothing put the plan back on the list, so restarting the
        server emptied the Loops tab while every `loop.json` sat there untouched. The
        queue, its per-video settings and the whole steering panel simply had no card
        to live on any more, and the answer to "where is my cycle" was that it was on
        disk and unreachable.

        It is adopted STOPPED, and never resumed on its own. A server is most often
        restarted to make something stop, and a restart that quietly picked six queued
        videos back up in the background is the opposite of what was asked. A plan whose
        file still says `running` is stamped stopped here too: the thread that wrote
        that word died with the process before it, and a card claiming to be working is
        a card the operator waits on forever. Press start and it goes on from its queue.
        """
        try:
            file = LoopFile.open(loop_dir)
            plan = file.read()
        except Exception:
            return None
        with self._lock:
            for existing in self.loops.values():
                if existing.file.dir == loop_dir:
                    return existing
        if plan.live:
            # said in the file, so the terminal and the next restart agree with the page
            plan = file.write_log(
                plan.model_copy(update={"status": "stopped",
                                        "note": "the server it was running in restarted"}))
        loop = Loop(id=uuid.uuid4().hex[:12], title=_title(plan.params),
                    file=file, params=plan.params,
                    started_at=loop_dir.stat().st_mtime,
                    finished_at=loop_dir.stat().st_mtime, _stop=True)
        with self._lock:
            self.loops[loop.id] = loop
        return loop

    def adopt_loops(self, output: Path, limit: int = 40) -> int:
        """Every loop plan under the output folder, newest first."""
        from ..pipeline.loop import all_loops

        return sum(1 for d in all_loops(Path(output), limit)
                   if self.adopt_loop(d) is not None)

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

    def forget(self, run_id: str, output: Path) -> str:
        """Take a settled run off the list, folder and all. Returns what was deleted.

        Forgetting it in memory alone would be a lie: `adopt_all` walks the output
        folder for `checkpoint.json` on every start, so a run removed from the list
        comes back with the next restart. The folder IS the run — hence a real delete,
        and hence a caller that has to mean it.

        Only a settled run. A live one is refused rather than raced: the worker thread
        writes checkpoints and part files under that folder, and pulling it out from
        under ffmpeg mid-stage produces a half-written video and a stack trace instead
        of an answer. Stop it first, which the page already offers.

        A PARKED run is deleted like any other, and the brief period when it was not is
        worth writing down. One went to `rmtree` from the row that was asking to be
        reviewed — the delete button sat beside the review button, unremarkable, two
        presses from throwing away the script the row existed to ask about — and the
        first repair was to refuse a parked run outright. That was the wrong end of it.
        A parked run is precisely the one an operator wants rid of, because it is the
        one that came out wrong; refusing it left no way to delete it at all, and the
        run that provoked all this had to be removed by hand. The ease was the fault,
        not the existence, so the guard is back to what it was and the page does the
        work instead: the second press on a parked run says what is about to be thrown
        away rather than asking a generic "sure?".

        The folder must sit INSIDE the output folder, and not BE it. This is the only
        place in slopgen that removes a tree the operator did not name, so it checks
        rather than trusts: a checkpoint carrying an edited path, or an output folder
        that moved since the run, must fail here rather than take a directory with it."""
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in ("running", "queued") or (run._stop and run.status == "stopping"):
            raise RuntimeError("stop it first — a running video is still writing files")
        target = run.run_dir or run.resume_dir
        removed = ""
        if target is not None:
            root = Path(output).resolve()
            d = Path(target).resolve()
            if d != root and root in d.parents and d.is_dir():
                shutil.rmtree(d)
                removed = str(d)
            elif d.exists():
                raise RuntimeError(f"{d} is not inside {root} — refusing to delete it")
        with self._lock:
            self.runs.pop(run_id, None)
        for loop in self.loops.values():
            if run_id in loop.run_ids:
                loop.run_ids.remove(run_id)
        # A page watching this run is holding a queue that will never be fed again.
        # Dropping the subscriptions ends those streams rather than leaving them on a
        # twenty-second keepalive forever.
        run._subs.clear()
        return removed

    # -- loops -------------------------------------------------------------

    def start_loop(self, params: RunParams, title: str = "", **control) -> Loop:
        """Begin a loop on these settings: one video at a time, steerable while it goes.

        The thread is its own rather than a pool slot. A loop lives for hours and spends
        most of them waiting — for a topic, for a review, for the video it started to
        finish — and a waiting loop holding one of two worker slots would be a loop that
        makes the server look busy while nothing is being made."""
        loop_dir = Path(self.store.global_cfg.paths.output) / loop_dir_name(params)
        file = LoopFile.create(loop_dir, params,
                               breakpoints=list(params.breakpoints), **control)
        loop = Loop(id=uuid.uuid4().hex[:12], title=title or _title(params),
                    file=file, params=params, started_at=time.time())
        with self._lock:
            self.loops[loop.id] = loop
        threading.Thread(target=self._loop_work, args=(loop,),
                         name=f"slopgen-loop-{loop.id}", daemon=True).start()
        return loop

    def retune_loop(self, loop_id: str, params: RunParams) -> Loop | None:
        """Rewrite the settings every next video is built from. What a loop may not
        change about itself is taken back off them by `loop.retune`, so a form that
        carries a mode and a count cannot smuggle either past this."""
        loop = self.loops.get(loop_id)
        if loop is None:
            return None
        loop.file.write_params(params)
        return loop

    def edit_loop(self, loop_id: str, **fields) -> Loop | None:
        """Change what the loop does next. Anything not named is left alone, and nothing
        reaches the video being made now — it was launched on the settings that stood
        when it started, which is the only way a setting can mean one thing for a whole
        video."""
        loop = self.loops.get(loop_id)
        if loop is None:
            return None
        add = fields.pop("add_topics", None)
        if add:
            loop.file.add_topics(add)
        loop.file.write_control(**fields)
        return loop

    def stock_loop(self, loop_id: str, n: int) -> Loop | None:
        """Ask the model for `n` topics NOW and queue them.

        The same thing a loop with `ahead` set does for itself, on a button — because
        wanting to see what it would come up with is not the same as wanting it to keep
        doing that, and a queue is easiest to judge when it is full. Failure is reported
        rather than swallowed: this one was asked for, and somebody is looking at it."""
        from ..pipeline.topics import proposer

        loop = self.loops.get(loop_id)
        if loop is None:
            return None
        plan = loop.file.read()
        fresh = proposer(self.store)(plan, n)
        if fresh:
            loop.file.add_topics([QueueItem(topic=t, by="ai") for t in fresh])
        return loop

    def stop_loop(self, loop_id: str, now: bool = False) -> bool:
        """End the loop. By default after the video it is making — that one is left to
        finish, because a video torn in half has spent its quota and produced nothing,
        and that is the promise `slopgen loop stop` makes in the README.

        Which means a stopped loop can sit there for the length of a whole video, and
        that is what `now` is for: the operator who wants it to stop NOW says so a second
        time, and the video in flight is stopped as well. It is a separate act — it
        throws away work — so it is a separate argument with its own button, rather than
        this one quietly changing its mind about what stop means.

        The flag goes on the loop AND in its file. In memory it is what this server's own
        thread reads between iterations; on disk it is what a terminal steering the same
        loop reads, and what survives this process dying — a loop asked to stop and then
        adopted back off disk must not come back running."""
        loop = self.loops.get(loop_id)
        if loop is None:
            return False
        loop._stop = True
        loop.file.write_control(stop=True)
        if now:
            for run_id in reversed(loop.run_ids):
                run = self.runs.get(run_id)
                if run is not None and run.status in ("queued", "running"):
                    self.stop(run_id)
                    break
        return True

    def resume_loop(self, loop_id: str) -> Loop | None:
        """Run an ended loop again on the plan it already has.

        There was no way to do this, and it was invisible until loops started coming
        back off disk (see :meth:`adopt_loop`): every loop on the page had been created
        moments earlier by the wizard, so "stopped" and "gone" looked the same and the
        card only ever needed a stop button. An adopted loop makes the gap plain — six
        topics queued, every setting editable, and nothing to press.

        `LoopFile.restart` is what makes this more than a thread: the reasons the loop
        ended are in the file, so a thread started without clearing them would read them
        and end again immediately. The queue is not touched — resuming means carrying on
        through the topics that are left, never making the finished ones over."""
        loop = self.loops.get(loop_id)
        if loop is None:
            return None
        if loop.file.read().live:
            raise RuntimeError("this loop is already running")
        loop.file.restart()
        loop._stop = False
        loop.started_at = time.time()
        loop.finished_at = 0.0
        threading.Thread(target=self._loop_work, args=(loop,),
                         name=f"slopgen-loop-{loop.id}", daemon=True).start()
        return loop

    def forget_loop(self, loop_id: str, output: Path) -> str:
        """Take an ended loop off the list and delete its plan folder.

        The videos it made are NOT touched. They are ordinary runs in folders of their
        own, they outlived the loop by design, and half of the reason to throw a loop
        away is that its queue was wrong while its output was fine — deleting six
        finished videos to tidy up a plan would be the worst possible reading of this
        button. What goes is `loop_*/loop.json` and the folder holding it.

        Refused while the loop is alive, for the same reason a run is: the thread writes
        that file every iteration. Stop it first."""
        loop = self.loops.get(loop_id)
        if loop is None:
            raise KeyError(loop_id)
        if loop.file.read().live:
            raise RuntimeError("stop it first — this loop is still going")
        removed = ""
        root = Path(output).resolve()
        d = Path(loop.file.dir).resolve()
        # the same check `forget` makes, and for the same reason: this removes a tree
        # nobody named, so it verifies rather than trusts
        if d != root and root in d.parents and d.is_dir():
            shutil.rmtree(d)
            removed = str(d)
        elif d.exists():
            raise RuntimeError(f"{d} is not inside {root} — refusing to delete it")
        with self._lock:
            self.loops.pop(loop_id, None)
        return removed

    def _loop_work(self, loop: Loop) -> None:
        def launch(params: RunParams, n: int) -> LaunchResult:
            run = self.start(params, title=f"{loop.title} #{n}", loop_id=loop.id)
            loop.run_ids.append(run.id)
            # The loop is the run's caller here, so it waits for it the way the terminal
            # does — except the run is on a pool thread, so waiting is watching. A run
            # that parks (review, pictures owed) settles too: it is finished as far as
            # this pass is concerned, and the loop decides what a parked one means.
            while run.status in ("queued", "running"):
                time.sleep(1.0)
            return LaunchResult(run.run_dir, run.status, run.message)

        from ..pipeline.topics import proposer

        try:
            LoopRunner(loop.file, launch, should_stop=lambda: loop._stop,
                       propose=proposer(self.store)).run()
        except Exception as e:  # a broken loop is a state, not a crash of the server
            log.exception("loop %s failed", loop.id)
            plan = loop.file.read()
            plan.status, plan.note = "failed", f"{type(e).__name__}: {e}"
            loop.file.write_log(plan)
        finally:
            loop.finished_at = time.time()

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
        """A stage reporting from inside its own loop — the only signal that a long one
        is moving at all.

        Kept on the run AND pushed to whoever is watching, because those answer
        different questions: the field is what a page opened halfway through reads out
        of `/api/runs`, and the push is what moves the bar without polling for it. The
        tick deliberately does NOT go into `run.events`. A drama generating eighty-four
        clips would otherwise spend the whole backlog on its own progress and push the
        log that explains the run out of the ring — and the same ticks would then be
        replayed into the log of every page that reconnects.

        Repeats are dropped rather than sent: several stages report after each item
        whether or not the count moved, and a bar redrawn at the same width is a wasted
        wake-up on every open tab."""
        if run.progress == (unit, done, total):
            return
        run.progress = (unit, done, total)
        # seq 0 on purpose: sequence numbers address the backlog, and a tick is never
        # in it. Borrowing the last real event's number would make two different things
        # answer to one id the moment anything starts reconnecting with `?after=`.
        self._push(run, Event(seq=0, at=time.time(), video=run.stage_video,
                              stage=PROGRESS_STAGE, status=unit,
                              message="", done=done, total=total))

    def _push(self, run: Run, ev: Event) -> None:
        """Hand one event to every watcher. No buffer, no state — see `_emit` for the
        things that also have to be remembered."""
        if self._loop is None:
            return
        for q in list(run._subs):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, ev)
            except RuntimeError:  # the loop went away under us
                pass

    def _emit(self, run: Run, video: int, stage: str, status: str, message: str) -> None:
        run._seq += 1
        ev = Event(seq=run._seq, at=time.time(), video=video, stage=stage,
                   status=status, message=message or "")
        run.events.append(ev)
        if status in ("paused", "review"):
            run.status, run.message = status, ev.message
        # A stage announcing itself is what the bar is labelled with, and what resets
        # it: the tally of the stage just finished must not sit under the name of the
        # one now starting. Run-level events (video -1, stage "run") are the run's own
        # lifecycle and name no stage.
        if status == "start" and video >= 0:
            run.stage, run.stage_video = stage, video
            run.progress = None
        elif status in ("done", "failed", "stopped") and stage == "run":
            run.stage, run.stage_video, run.progress = "", -1, None
        self._push(run, ev)

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
        for loop in self.loops.values():
            loop._stop = True
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
    return outcome(orch.run_dir, run.params.count) or "done"


def _title(p: RunParams) -> str:
    bits = [p.mode]
    if p.fandom:
        bits.append(p.fandom)
    if p.count > 1:
        bits.append(f"×{p.count}")
    return " · ".join(bits)
