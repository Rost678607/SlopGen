"""Stage 6: prepare the overlay ad spec (native ads were handled in script/footage).

Picks a random animation from the contract's overlay assets dir and clamps the
schedule to the actual video duration.
"""

from __future__ import annotations

import random

from ...media.ffmpeg import OverlaySpec
from ..context import AppContext
from ..job import VideoJob

OVERLAY_EXTS = {".webm", ".gif", ".png", ".jpg", ".jpeg", ".mp4", ".mov"}


def build_overlay_spec(job: VideoJob, ctx: AppContext) -> OverlaySpec | None:
    if not ctx.overlay_ad_on:
        return None
    ov = ctx.ad.overlay
    assets = (
        [p for p in ov.assets_dir.iterdir() if p.suffix.lower() in OVERLAY_EXTS]
        if ov.assets_dir.is_dir()
        else []
    )
    if not assets:
        raise FileNotFoundError(f"no overlay ad assets in {ov.assets_dir}")
    total = job.total_duration
    start = min(ov.start_s, max(total - ov.duration_s - 1, 0))
    dur = min(ov.duration_s, max(total - start - 0.5, 1))
    return OverlaySpec(
        asset=random.choice(assets),
        width=ov.width,
        position=ov.position,
        start_s=start,
        duration_s=dur,
        text=ov.text,
    )
