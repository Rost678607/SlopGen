"""Crash-safe checkpointing: persist pipeline progress so a run that dies
mid-way can be resumed instead of regenerated from scratch.

One JSON file per run (``<run_dir>/checkpoint.json``) records the resolved run
params plus, for every video, which stages already finished, the serialized
job state, and — if it crashed — the stage that failed and the error. Resuming
reloads this file, skips the finished stages, and re-runs from the point of
failure. The completed stages' outputs (TTS audio, downloaded footage, the
serialized job itself) all live on disk, so nothing is recomputed needlessly.

Writes are atomic (temp file + os.replace) so a crash mid-write can never
corrupt the checkpoint.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from ..config import RunParams
from .job import VideoJob

CHECKPOINT_NAME = "checkpoint.json"


class Checkpoint:
    """Read/write access to a single run's checkpoint file."""

    def __init__(self, run_dir: Path, data: dict):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / CHECKPOINT_NAME
        self.data = data

    # -- construction ------------------------------------------------------

    @classmethod
    def start(cls, run_dir: Path, params: RunParams, stages: list[str]) -> "Checkpoint":
        cp = cls(run_dir, {
            "version": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "params": params.model_dump(mode="json"),
            "stages": stages,
            "jobs": {},
        })
        cp.save()
        return cp

    @classmethod
    def load(cls, run_dir: Path) -> "Checkpoint":
        path = Path(run_dir) / CHECKPOINT_NAME
        if not path.exists():
            raise FileNotFoundError(f"no checkpoint at {path}")
        return cls(run_dir, json.loads(path.read_text()))

    @property
    def params(self) -> RunParams:
        return RunParams.model_validate(self.data["params"])

    # -- per-job reads -----------------------------------------------------

    def _job_state(self, index: int) -> dict:
        return self.data.setdefault("jobs", {}).get(str(index), {})

    def status(self, index: int) -> str:
        """pending | running | paused | review | failed | done."""
        return self._job_state(index).get("status", "pending")

    def manual_msg(self, index: int) -> str:
        """Operator-facing note left when a job paused for manual clips."""
        return self._job_state(index).get("manual_msg", "")

    def review_stage(self, index: int) -> str:
        """The stage this job is parked on for review (empty when it isn't)."""
        return self._job_state(index).get("review_stage") or ""

    def reviewed(self, index: int) -> list[str]:
        """Breakpoints already shown for this video — they never fire twice."""
        return list(self._job_state(index).get("reviewed", []))

    def completed(self, index: int) -> list[str]:
        """Stages already finished for this video (fresh mutable copy)."""
        return list(self._job_state(index).get("completed", []))

    def load_job(self, index: int) -> VideoJob | None:
        st = self._job_state(index)
        return VideoJob.model_validate(st["job"]) if st.get("job") else None

    # -- per-job writes ----------------------------------------------------

    def _put(self, job: VideoJob, done: list[str], status: str, **extra) -> None:
        prev = self._job_state(job.index)
        st = {
            "status": status,
            "completed": list(done),
            # carried across every write: a breakpoint already shown must not fire again
            "reviewed": prev.get("reviewed", []),
            "job": job.model_dump(mode="json"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            **extra,
        }
        self.data.setdefault("jobs", {})[str(job.index)] = st
        self.save()

    def stage_done(self, job: VideoJob, done: list[str]) -> None:
        self._put(job, done, "running", failed_stage=None, error=None)

    def finished(self, job: VideoJob, done: list[str]) -> None:
        self._put(job, done, "done", failed_stage=None, error=None)

    def failed(self, job: VideoJob, done: list[str], stage: str, error: str) -> None:
        self._put(job, done, "failed", failed_stage=stage, error=error)

    def paused(self, job: VideoJob, done: list[str], stage: str, message: str) -> None:
        """Park a job that needs operator input (manual clips). Not a failure: the
        stage is left unfinished so a plain --resume re-runs it once clips arrive."""
        self._put(job, done, "paused", failed_stage=None, error=None, manual_msg=message)

    def awaiting_review(self, job: VideoJob, done: list[str], stage: str) -> None:
        """Park a job on a breakpoint: `stage` finished and its output is waiting to
        be inspected. Also not a failure — the stage stays completed."""
        self._put(job, done, "review", failed_stage=None, error=None, review_stage=stage)

    def review_done(self, job: VideoJob, done: list[str], stage: str, rerun: bool) -> None:
        """Store the reviewed (possibly edited) job and release it. `rerun` drops the
        stage from the completed list — used when the edit made its output stale (e.g.
        narration text changed under the TTS breakpoint) — while `reviewed` keeps the
        breakpoint from firing a second time on that re-run."""
        kept = [s for s in done if not (rerun and s == stage)]
        self._put(
            job, kept, "running",
            failed_stage=None, error=None, review_stage=None,
            reviewed=self.reviewed(job.index) + [stage],
        )

    # -- persistence -------------------------------------------------------

    def save(self) -> None:
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1))
        os.replace(tmp, self.path)
