"""Orchestrator: runs the stage chain for each video in a batch.

Progress is reported through an on_event callback so both the CLI printer and
the TUI dashboard can consume the same stream. One failed video does not stop
the batch.

Every run is checkpointed (see checkpoint.py): after each completed stage the
job state is written to ``<run_dir>/checkpoint.json``, and on failure the stage
that died and the error are recorded. A crashed run can be resumed with
``run(resume_dir=...)``, which skips already-finished stages and continues from
the point of failure.

The same machinery carries breakpoints (see review.py): when a stage listed in
``params.breakpoints`` finishes, the job is parked in a ``review`` checkpoint and
the batch moves on to the next video. Resuming after the operator has inspected —
and possibly edited — the result continues from there.

And it carries the part-by-part flow (see parts.py). The tail of a drama runs one
episode at a time: a stage that still owes the episodes whose hand-made clips have
not arrived is deliberately left OUT of the completed list, so the next resume walks
back into it and picks up whatever has turned up since. Each episode is published the
moment it is cut, and remembering that on the part itself is what stops a resume from
uploading it twice.
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..publish import get_publisher
from . import parts, review
from .checkpoint import Checkpoint
from .context import AppContext
from .job import VideoJob
from .manual import ManualInputPending
from .stages import (
    assemble,
    cut,
    drama_footage,
    drama_script,
    entities,
    footage,
    idea,
    metadata,
    script,
    subtitles,
    tts,
)

# (stage name, callable(job, ctx)). The drama chain drops idea (the premise IS the
# input), swaps in the drama script/footage stages and adds two of its own: `entities`
# (the visual registry) and `cut` (where the episode boundaries settle, right before
# anything is generated). The names it shares with the info chain are shared exactly
# so checkpoints/resume stay uniform (the mode lives in params).
STAGES_INFO: list[tuple[str, Callable]] = [
    ("idea", idea.run),
    ("script", script.run),
    ("tts", tts.run),
    ("footage", footage.run),
    ("subtitles", subtitles.run),
    ("assemble", assemble.run),
    ("metadata", metadata.run),
]
STAGES_DRAMA: list[tuple[str, Callable]] = [
    ("script", drama_script.run),
    ("entities", entities.run),
    ("tts", tts.run),
    ("cut", cut.run),
    ("footage", drama_footage.run),
    ("subtitles", subtitles.run),
    ("assemble", assemble.run),
    ("metadata", metadata.run),
]


def stages_for(params) -> list[tuple[str, Callable]]:
    return STAGES_DRAMA if params.mode == "drama" else STAGES_INFO


# on_event(video_index, stage, status, message); status: start|done|error|skip|paused|review
EventCallback = Callable[[int, str, str, str], None]


class Orchestrator:
    def __init__(self, ctx: AppContext, on_event: EventCallback | None = None):
        self.ctx = ctx
        self.on_event = on_event or (lambda *a: None)
        self.run_dir: Path | None = None  # set once run() picks/receives it

    def _run_dir(self) -> Path:
        p = self.ctx.params
        base = p.out or self.ctx.g.paths.output
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(base) / f"{stamp}_{p.content_type or p.mode}_{p.lang}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _publish(self, i: int, job: VideoJob, cp: Checkpoint, done: list[str]) -> None:
        """Send out every episode that is cut and has not gone yet.

        One part at a time, and each is recorded on the part as soon as it lands: an
        upload is the one thing here that cannot be undone, and a drama is resumed
        many times over the days it takes to hand-make its clips, so a part already
        published must never be offered again. `publish` only counts as a finished
        stage once no episode is left waiting for one."""
        if "publish" in done:
            return
        publisher = None
        for part in job.parts:
            if part.published or not part.file:
                continue
            if self.ctx.params.dry_run:
                self.on_event(i, "publish", "skip", f"dry run · part {part.number}")
                part.published = str(part.file)
                continue
            self.on_event(i, "publish", "start", f"part {part.number}")
            publisher = publisher or get_publisher(self.ctx)
            part.published = publisher.publish(job, part, self.ctx)
            self.on_event(i, "publish", "done", part.published)
            cp.stage_done(job, done)  # persist it the moment it is out of our hands
        if job.parts and all(p.published for p in job.parts):
            done.append("publish")

    def run(self, resume_dir: Path | None = None) -> list[VideoJob]:
        p = self.ctx.params
        stages = stages_for(p)
        if resume_dir is not None:
            run_dir = Path(resume_dir)
            cp = Checkpoint.load(run_dir)
        else:
            run_dir = self._run_dir()
            cp = Checkpoint.start(run_dir, p, [n for n, _ in stages] + ["publish"])
        self.run_dir = run_dir
        jobs: list[VideoJob] = []

        for i in range(p.count):
            # already finished on a previous run — nothing to redo
            if cp.status(i) == "done":
                job = cp.load_job(i)
                if job is not None:
                    jobs.append(job)
                self.on_event(i, "publish", "skip", "already done")
                continue

            job = cp.load_job(i) or VideoJob(index=i, workdir=run_dir / f"{i:02d}")
            job.workdir.mkdir(parents=True, exist_ok=True)
            jobs.append(job)
            done = cp.completed(i)  # ordered list of finished stages
            breakpoints = review.wanted(p.breakpoints, p.mode) - set(cp.reviewed(i))
            current = ""
            parked = False
            try:
                for name, fn in stages:
                    if name in done:  # resumed: output already on disk
                        self.on_event(i, name, "skip", "resumed")
                        continue
                    current = name
                    self.on_event(i, name, "start", "")
                    t0 = time.monotonic()
                    fn(job, self.ctx)
                    self.on_event(i, name, "done", f"{time.monotonic() - t0:.1f}s")
                    if name in parts.PART_STAGES and job.pending_parts:
                        # it did the episodes it could and still owes the rest, so it
                        # must NOT count as finished: leaving it out of `done` is what
                        # sends the next resume back into it. Its output is saved all
                        # the same — that is the part that is already cut.
                        cp.stage_done(job, done)
                        continue
                    done.append(name)
                    cp.stage_done(job, done)
                    if name in breakpoints:  # park for review; the run continues later
                        cp.awaiting_review(job, done, name)
                        self.on_event(i, name, "review", "breakpoint")
                        parked = True
                        break
                if parked:
                    continue

                self._publish(i, job, cp, done)
                if job.pending_parts:  # the rest of the drama is still to be made
                    msg = parts.pending_message(job)
                    cp.paused(job, done, current, msg)
                    self.on_event(i, current, "paused", msg)
                    continue

                cp.finished(job, done)
                self.ctx.append_history({
                    "topic": job.topic,
                    "lang": p.lang,
                    "content_type": p.content_type,
                    "date": datetime.now().isoformat(timespec="seconds"),
                    "result": job.published,
                })
            except ManualInputPending as e:  # not a failure — awaiting operator clips
                cp.paused(job, done, current, str(e))
                self.on_event(i, current, "paused", str(e))
            except Exception as e:  # keep the batch alive; remember where it died
                cp.failed(job, done, current, str(e))
                self.on_event(i, "error", "error", f"{e}\n{traceback.format_exc(limit=3)}")
        return jobs
