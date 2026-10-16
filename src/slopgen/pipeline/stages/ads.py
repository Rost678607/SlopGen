"""Stage 6: prepare the overlay ad spec (native ads were handled in script/footage).

Picks a random animation from the contract's overlay assets dir and clamps the
schedule to the actual video duration.
"""

from __future__ import annotations

import random

from pathlib import Path

from ...media.ffmpeg import OverlaySpec, video_dims
from ..context import AppContext
from ..job import VideoJob

OVERLAY_EXTS = {".webm", ".gif", ".png", ".jpg", ".jpeg", ".mp4", ".mov"}


def overlay_asset(job: VideoJob, ctx: AppContext) -> Path:
    """Which of the contract's banners this video carries. Seeded on the run, so the
    room's preview shows the banner the cut will — a fresh roll on every call put a
    different one in each."""
    ov = ctx.ad.overlay
    assets = sorted(
        (p for p in ov.assets_dir.iterdir() if p.suffix.lower() in OVERLAY_EXTS)
        if ov.assets_dir.is_dir() else [])
    if not assets:
        raise FileNotFoundError(f"no overlay ad assets in {ov.assets_dir}")
    return random.Random(f"ad|{job.workdir}").choice(assets)


# The corners a contract could name, as the margins the delivery pass always used.
_MARGIN, _TOP, _BOTTOM = 40, 140, 420


def overlay_box(ctx: AppContext, asset: Path) -> tuple[int, int, int, int]:
    """Where the banner goes, in pixels of the frame: (x, y, width, height).

    The run's own box when it has one (`RunParams.ad_box`, dragged into place),
    otherwise the contract's corner and width as they always were — which is also
    where the box starts out when somebody first drags it."""
    v = ctx.g.video
    aw, ah = video_dims(asset)
    aspect = (ah / aw) if aw else 1.0
    box = ctx.params.ad_box
    if box is not None:
        w = max(16, round(box.w * v.width))
        h = round(w * aspect)
        x, y = round(box.x * v.width), round(box.y * v.height)
    else:
        ov = ctx.ad.overlay
        w = ov.width
        h = round(w * aspect)
        x = _MARGIN if ov.position.endswith("left") else v.width - w - _MARGIN
        y = _TOP if ov.position.startswith("top") else v.height - h - _BOTTOM
    w = min(w, v.width)
    x = min(max(x, 0), v.width - w)
    y = min(max(y, 0), max(v.height - h, 0))
    return x, y, w, h


def overlay_window(job: VideoJob, ctx: AppContext) -> tuple[float, float]:
    """When the banner is up: the contract's moment and length, clamped to the video."""
    ov = ctx.ad.overlay
    total = job.total_duration
    start = min(ov.start_s, max(total - ov.duration_s - 1, 0))
    return start, min(ov.duration_s, max(total - start - 0.5, 1))


def build_overlay_spec(job: VideoJob, ctx: AppContext) -> OverlaySpec | None:
    if not ctx.overlay_ad_on:
        return None
    ov = ctx.ad.overlay
    asset = overlay_asset(job, ctx)
    x, y, w, _h = overlay_box(ctx, asset)
    start, dur = overlay_window(job, ctx)
    return OverlaySpec(
        asset=asset,
        width=w,
        position=ov.position,
        start_s=start,
        duration_s=dur,
        text=ov.text,
        x=x,
        y=y,
    )
