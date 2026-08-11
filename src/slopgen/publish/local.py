"""Local 'publisher': the video already sits in its workdir; just report the path."""

from __future__ import annotations

from ..pipeline.context import AppContext
from ..pipeline.job import Part, VideoJob


class LocalPublisher:
    def publish(self, job: VideoJob, part: Part, ctx: AppContext) -> str:
        return str(part.file) if part.file else ""
