"""Stage 7: build scene segments, concat, then the final ffmpeg composition
(burned subtitles + background music + ad overlay)."""

from __future__ import annotations

import random
import shutil

from ...media import ffmpeg
from ...media.stock import VIDEO_EXTS
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


def run(job: VideoJob, ctx: AppContext) -> None:
    tmp = job.workdir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    vis = ctx.visuals

    segments = []
    for i, scene in enumerate(job.scenes):
        # 1) silent background parts (video loops / Ken-Burns photos)
        bg_parts = []
        for k, a in enumerate(scene.bg_assets):
            part = tmp / f"s{i:02d}_bg{k}.mp4"
            if a.is_photo:
                ffmpeg.make_photo_part(a.path, a.duration, part, ctx.g, vis.background.motion, direction=k)
            else:
                ffmpeg.make_video_part(a.path, a.duration, part, ctx.g, start=a.start)
            bg_parts.append(part)
        # 2) compose scene with voiceover + foreground inserts. In drama mode the
        # clip length is the master, so the voice is time-stretched (atempo) to it.
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
        segments.append(seg)

    concat_path = tmp / "concat.mp4"
    ffmpeg.concat(segments, concat_path)

    final = job.workdir / "final.mp4"
    fonts_dir = ctx.g.paths.assets / "fonts"
    ffmpeg.finalize(
        concat_path,
        final,
        ctx.g,
        ass=job.ass_path,
        music=_pick_music(ctx),
        overlay=build_overlay_spec(job, ctx),
        fonts_dir=fonts_dir if fonts_dir.is_dir() else None,
    )
    job.final_path = final

    if not ctx.params.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
