"""Publisher interface. Implementations: local, youtube, tiktok (stub)."""

from __future__ import annotations

from typing import Protocol

from ..pipeline.context import AppContext
from ..pipeline.job import Part, VideoJob


class Publisher(Protocol):
    def publish(self, job: VideoJob, part: Part, ctx: AppContext) -> str:
        """Upload/save ONE finished part; return its URL or local path.

        A part at a time, not a whole job: a drama's episodes are cut days apart as
        the operator hand-makes their clips, and each goes out the moment it is
        ready. `job` comes along for the context an uploader may want (the topic,
        the cast); what to upload is `part`."""
        ...


def get_publisher(ctx: AppContext) -> "Publisher":
    from . import local, tiktok, youtube

    acc = ctx.account
    if acc is None:
        return local.LocalPublisher()
    if acc.platform == "youtube":
        return youtube.YouTubePublisher()
    if acc.platform == "tiktok":
        return tiktok.TikTokPublisher()
    return local.LocalPublisher()
