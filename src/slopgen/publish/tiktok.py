"""TikTok publisher — intentionally a stub.

There is no official upload API for regular accounts; a cookie-based browser
automation uploader is planned. Until then, use platform="local" and upload
by hand, or implement `publish` here.
"""

from __future__ import annotations

from ..pipeline.context import AppContext
from ..pipeline.job import VideoJob


class TikTokPublisher:
    def publish(self, job: VideoJob, ctx: AppContext) -> str:
        paths = job.final_paths or ([job.final_path] if job.final_path else [])
        raise NotImplementedError(
            "TikTok upload is not implemented yet — video saved locally at:\n"
            + "\n".join(str(p) for p in paths)
            + "\nupload it manually"
        )
