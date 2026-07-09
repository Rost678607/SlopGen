"""Orchestrator: runs the stage chain for each video in a batch.

Progress is reported through an on_event callback so both the CLI printer and
the TUI dashboard can consume the same stream. One failed video does not stop
the batch.

Every run is checkpointed (see checkpoint.py): after each completed stage the
job state is written to ``<run_dir>/checkpoint.json``, and on failure the stage
that died and the error are recorded. A crashed run can be resumed with
``run(resume_dir=...)``, which skips already-finished stages and continues from
the point of failure.
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..publish import get_publisher
from .checkpoint import Checkpoint
from .context import AppContext
from .job import VideoJob
from .stages import (
    assemble,
    drama_footage,
    drama_script,
    footage,
    idea,
    metadata,
    script,
    subtitles,
    tts,
)

# (stage name, callable(job, ctx)). The drama chain drops idea (the premise IS the
# input) and swaps in the drama script/footage stages; stage NAMES are shared with
# the info chain so checkpoints/resume stay uniform (the mode lives in the params).
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
    ("tts", tts.run),
    ("footage", drama_footage.run),
    ("subtitles", subtitles.run),
    ("assemble", assemble.run),
    ("metadata", metadata.run),
]


def stages_for(params) -> list[tuple[str, Callable]]:
    return STAGES_DRAMA if params.mode == "drama" else STAGES_INFO

# on_event(video_index, stage, status, message); status: start|done|error|skip
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
            current = ""
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
                    done.append(name)
                    cp.stage_done(job, done)

                if "publish" not in done:
                    current = "publish"
                    if p.dry_run:
                        self.on_event(i, "publish", "skip", "dry run")
                        job.published = str(job.final_path)
                    else:
                        self.on_event(i, "publish", "start", "")
                        job.published = get_publisher(self.ctx).publish(job, self.ctx)
                        self.on_event(i, "publish", "done", job.published)
                    done.append("publish")

                cp.finished(job, done)
                self.ctx.append_history({
                    "topic": job.topic,
                    "lang": p.lang,
                    "content_type": p.content_type,
                    "date": datetime.now().isoformat(timespec="seconds"),
                    "result": job.published,
                })
            except Exception as e:  # keep the batch alive; remember where it died
                cp.failed(job, done, current, str(e))
                self.on_event(i, "error", "error", f"{e}\n{traceback.format_exc(limit=3)}")
        return jobs
