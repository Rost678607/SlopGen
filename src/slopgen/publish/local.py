"""Local 'publisher': the video already sits in its workdir; just report the path."""

from __future__ import annotations

from ..pipeline.context import AppContext
from ..pipeline.job import VideoJob


class LocalPublisher:
    def publish(self, job: VideoJob, ctx: AppContext) -> str:
        return str(job.final_path)
