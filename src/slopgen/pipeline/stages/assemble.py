"""Stage 7: build scene segments, concat, then the final ffmpeg composition
(burned subtitles + background music + ad overlay).

One file per part. There is no separate single-video path: an info clip is a drama
with one part, so both go through the same loop and differ only in what the file is
called. The stage is re-enterable — it cuts the episodes that are ready and have not
been cut yet — which is what lets a hand-made drama be published one episode at a
time while the rest is still being generated (see :mod:`..parts`).
"""

from __future__ import annotations

import random
import shutil

from ...media import ffmpeg
from .. import parts
from ..context import AppContext
from ..job import VideoJob
from .ads import build_overlay_spec

MUSIC_EXTS = {".mp3", ".m4a", ".ogg", ".wav", ".flac"}


def _pick_music(ctx: AppContext):
    music_dir = ctx.g.paths.assets / "music"
    if not music_dir.is_dir():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in MUSIC_EXTS]
    return random.choice(tracks) if tracks else None


FG_Y = {"center": "(H-h)/2", "top": "220", "bottom": "H-h-560"}


def _segment(i: int, scene, tmp, ctx: AppContext):
    """Render one scene to a self-contained segment: background, voice, inserts."""
    vis = ctx.visuals
    bg_parts = []
    for k, a in enumerate(scene.bg_assets):
        part = tmp / f"s{i:02d}_bg{k}.mp4"
        if a.is_photo:
            ffmpeg.make_photo_part(a.path, a.duration, part, ctx.g, vis.background.motion, direction=k)
        else:
            ffmpeg.make_video_part(a.path, a.duration, part, ctx.g, start=a.start, speed=a.speed)
        bg_parts.append(part)
    # in drama mode the clip length is the master, so the voice is time-stretched to it
    voice = scene.audio
    if scene.audio and abs(scene.audio_tempo - 1.0) > 0.02:
        voice = tmp / f"s{i:02d}_voice.m4a"
        ffmpeg.stretch_audio(scene.audio, voice, scene.audio_tempo)
    seg = tmp / f"seg_{i:02d}.mp4"
    ffmpeg.make_scene_segment(
        bg_parts,
        voice,
        scene.duration,
        seg,
        ctx.g,
        fg_inserts=[(f.path, f.start, f.duration, f.is_video) for f in scene.fg_inserts],
        fg_width=int(ctx.g.video.width * vis.foreground.width_pct / 100),
        fg_y=FG_Y[vis.foreground.position],
        tmp=tmp,
    )
    return seg


def run(job: VideoJob, ctx: AppContext) -> None:
    tmp = job.workdir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    parts.sync(job)
    # only the episodes whose clips are in, and among those only the ones not already
    # cut — a drama is finished a part at a time, and re-cutting part 1 on the resume
    # that brings part 2 would spend the whole encode again for the same file
    todo = [p for p in parts.ready(job) if p.file is None]
    fonts_dir = ctx.g.paths.assets / "fonts"
    fonts = fonts_dir if fonts_dir.is_dir() else None
    music = _pick_music(ctx)
    multi = len(job.parts) > 1

    at = {id(scene): i for i, scene in enumerate(job.scenes)}
    groups = [(part, parts.scenes_by_part(job.scenes, part.number)) for part in todo]
    total = sum(len(scenes) for _, scenes in groups)
    made = 0
    for n, (part, scenes) in enumerate(groups, start=1):
        if not scenes:
            continue
        segments = []
        for scene in scenes:
            segments.append(_segment(at[id(scene)], scene, tmp, ctx))
            made += 1
            ctx.progress("assemble", made, total)
        final = job.workdir / (f"part_{part.number:02d}.mp4" if multi else "final.mp4")
        # the overlay ad is scheduled against the video it rides on, so it is given
        # this episode's scenes — its total_duration is the episode's, not the drama's
        part_job = job.model_copy(update={"scenes": scenes})
        ffmpeg.finalize(
            segments,
            final,
            ctx.g,
            ass=part.ass,
            music=music,
            overlay=build_overlay_spec(part_job, ctx),
            fonts_dir=fonts,
            tmp=tmp,
            on_progress=ctx.progress,
        )
        part.file = final
        ctx.progress("finalize", n, len(groups))

    if not job.final_paths:
        raise ValueError("nothing was assembled — no part has a single scene with footage")

    if not ctx.params.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
