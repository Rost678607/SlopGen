"""Publisher interface. Implementations: local, youtube, tiktok (stub)."""

from __future__ import annotations

from typing import Protocol

from ..pipeline.context import AppContext
from ..pipeline.job import VideoJob


class Publisher(Protocol):
    def publish(self, job: VideoJob, ctx: AppContext) -> str:
        """Upload/save the finished video(s); return URL(s) or local path(s)."""
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
