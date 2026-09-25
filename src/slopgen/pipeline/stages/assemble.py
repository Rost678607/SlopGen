"""Stage 7: build scene segments, concat, then the final ffmpeg composition
(burned subtitles + background music + ad overlay).

One file per part. There is no separate single-video path: an info clip is a drama
with one part, so both go through the same loop and differ only in what the file is
called. The stage is re-enterable — it cuts the episodes that are ready and have not
been cut yet — which is what lets a hand-made drama be published one episode at a
time while the rest is still being generated (see :mod:`..parts`).
"""

from __future__ import annotations

import logging
import random
import shutil
from pathlib import Path

from ...media import ffmpeg
from .. import parts
from ..context import AppContext
from ..job import VideoJob
from .ads import build_overlay_spec

log = logging.getLogger(__name__)

MUSIC_EXTS = {".mp3", ".m4a", ".ogg", ".wav", ".flac"}


# What the operator picked, when what they picked is silence. A reserved name rather
# than a second field: the question has three answers and a select with three entries
# is how it is asked, so it is one value all the way down.
MUSIC_NONE = "none"


def music_root(cfg) -> Path:
    return cfg.paths.assets / "music"


def tracks_in(cfg) -> list:
    """Every music file under `assets/music/`, subfolders included, in a fixed order.

    SORTED, because the order is what the roll below indexes into: a folder listed in
    whatever order the filesystem hands back would give one machine a different track
    from another for the same video. Sorted by the path RELATIVE to the root rather
    than by bare name, so the order groups by folder — which is the order the operator
    sees in the select and the order a folder-wide roll indexes into."""
    d = music_root(cfg)
    if not d.is_dir():
        return []
    return sorted((p for p in d.rglob("*")
                   if p.is_file() and p.suffix.lower() in MUSIC_EXTS),
                  key=lambda p: p.relative_to(d).as_posix())


def track_key(cfg, path: Path) -> str:
    """What the operator's choice calls this track: its path under `assets/music/`.

    A track sitting at the root keeps its bare file name, which is what every stored
    choice from before there were folders says — so those keep resolving to the same
    file instead of quietly falling back to the roll."""
    try:
        return path.relative_to(music_root(cfg)).as_posix()
    except ValueError:
        return path.name


def folders_in(cfg) -> list[str]:
    """Every folder under `assets/music/` that holds a track, as a choice value.

    The TRAILING SLASH is the whole of the distinction between "this folder, rolled"
    and "this exact track" — one namespace and one select, the way `none` is a
    reserved value rather than a second field. A folder is listed as soon as anything
    below it is a track, nesting included, because a roll over a folder is a roll over
    everything under it."""
    d = music_root(cfg)
    out: set[str] = set()
    for p in tracks_in(cfg):
        rel = p.relative_to(d).parent
        while rel != Path("."):
            out.add(rel.as_posix() + "/")
            rel = rel.parent
    return sorted(out)


def tracks_under(cfg, folder: str) -> list:
    """The tracks a folder choice rolls over — itself and everything nested in it."""
    want = folder if folder.endswith("/") else folder + "/"
    return [p for p in tracks_in(cfg) if track_key(cfg, p).startswith(want)]


def music_for(params, cfg, job: VideoJob | None = None):
    """The track that plays under this video, or None for silence.

    Four answers now (see `RunParams.music`), and the interesting one is still the
    default. It used to be `random.choice`, which is fine for a pipeline nobody watches
    and wrong the moment there is a montage room: the room would have to either play
    nothing or play a track the cut then did not use. So the roll is SEEDED on the run
    — the same video draws the same track every time, on every machine, before and
    after a resume — and the room can simply ask this function what the render is going
    to do.

    A FOLDER narrows what that roll draws from without giving up the draw: the seed is
    the run either way, so picking `эпик/` still lands the room and the render on the
    same track, and re-picking simply rolls over a different shelf.

    A choice that no longer matches anything falls back to the roll rather than to
    silence, whether it was a renamed file or an emptied folder: a moved asset should
    cost the choice, not the music.

    It takes the parameters and the config rather than an `AppContext` because the
    montage room asks it on every reply it sends, and building a context there would
    open an LLM client to answer a question about a folder."""
    want = (getattr(params, "music", "") or "").strip()
    if want == MUSIC_NONE:
        return None
    tracks = tracks_in(cfg)
    if not tracks:
        return None
    pool = tracks
    if want.endswith("/"):
        pool = tracks_under(cfg, want)
        if not pool:
            log.warning("music: %r holds no tracks any more — rolling over all of them",
                        want)
            pool = tracks
    elif want:
        named = next((p for p in tracks if track_key(cfg, p) == want), None)
        if named is not None:
            return named
        log.warning("music: %r is not in assets/music any more — rolling instead", want)
    seed = str(job.workdir) if job is not None else str(getattr(params, "fandom", ""))
    return random.Random(f"music|{seed}").choice(pool)


def _pick_music(ctx: AppContext, job: VideoJob | None = None):
    return music_for(ctx.params, ctx.g, job)


FG_Y = {"center": "(H-h)/2", "top": "220", "bottom": "H-h-560"}


def _segment(i: int, scene, tmp, ctx: AppContext):
    """Render one scene to a self-contained segment: background, voice, inserts."""
    vis = ctx.visuals
    bg_parts = []
    for k, a in enumerate(scene.bg_assets):
        part = tmp / f"s{i:02d}_bg{k}.mp4"
        if a.is_photo:
            ffmpeg.make_photo_part(a.path, a.duration, part, ctx.g, vis.background.motion,
                                   direction=k, move=a.move, phase=a.move_at,
                                   fit=a.fit, ax=a.fit_x, ay=a.fit_y)
        else:
            ffmpeg.make_video_part(a.path, a.duration, part, ctx.g, start=a.start,
                                   speed=a.speed, move=a.move, phase=a.move_at)
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
    music = _pick_music(ctx, job)
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
            # the run's montage filters, laid over this episode end to end
            fx=ctx.params.filters,
            tmp=tmp,
            on_progress=ctx.progress,
        )
        part.file = final
        ctx.progress("finalize", n, len(groups))

    if not job.final_paths:
        raise ValueError("nothing was assembled — no part has a single scene with footage")

    if not ctx.params.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
