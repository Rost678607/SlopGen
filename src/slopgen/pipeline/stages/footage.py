"""Stage 4: fill every scene's background assets and foreground inserts
according to the active visuals profile.

Background sources: narration-linked or neutral stock video, stock photos
(sliced by interval for Ken Burns), local dirs (e.g. gameplay loops). A video
background can be `continuous`: one clip played straight through, each scene
reading the next slice, so gameplay doesn't restart per scene.
Foreground: periodic narration-linked inserts (photo or short video clip).
AI sources (ai_photo / ai_video) generate the asset from the narration query
via the free providers in media/generate.py, falling back to the stock chain.
"""

from __future__ import annotations

import itertools
import os
import random
import re
from pathlib import Path

from ...config.models import VisualsConfig
from ...media.generate import (
    DEFAULT_VIDEO_SPACES,
    PHOTO_MODELS,
    VIDEO_MODELS,
    GenParams,
)
from ...media.stock import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    FootageError,
    _local,
    find_clip,
    find_image,
)
from .. import manual
from ..context import AppContext
from ..job import BgAsset, FgInsert, Scene, VideoJob, Word

FG_PAD_S = 0.25  # breathing room around the anchored phrase
FG_MIN_S = 1.2   # never flash an insert shorter than this


def _queries(scene: Scene, n: int, fallback: list[str]) -> list[str]:
    """n narration-synced queries: LLM beats first, padded with keywords/fallback."""
    pool = scene.visual_queries + [" ".join(scene.keywords)] + scene.keywords + fallback
    pool = [q for q in pool if q.strip()]
    return list(itertools.islice(itertools.cycle(pool), n)) if pool else [""] * n


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", s.lower())


def _anchor_phrase(phrase: str, words: list[Word], scene_start: float, scene_dur: float) -> tuple[float, float] | None:
    """Locate `phrase` within the scene's word timings; return (start, end)
    scene-relative seconds covering the words that make up the phrase, or None."""
    ptoks = [t for t in (_norm(w) for w in phrase.split()) if len(t) > 2]
    if not ptoks or not words:
        return None
    pset = set(ptoks)
    hits = [i for i, w in enumerate(words) if _norm(w.text) in pset]
    if not hits:
        return None
    first, last = words[hits[0]], words[hits[-1]]
    start = max(first.start - scene_start - FG_PAD_S, 0.0)
    end = min(last.end - scene_start + FG_PAD_S, scene_dur)
    if end - start < FG_MIN_S:
        end = min(start + FG_MIN_S, scene_dur)
        start = max(end - FG_MIN_S, 0.0)
    return start, end


def _gen(ctx: AppContext, ai_model: str = "") -> GenParams:
    """Build AI-generation params from the footage config + video dimensions.
    `ai_model` (from the visuals profile) overrides the pollinations model /
    video Space chain when the source is ai_photo / ai_video."""
    f = ctx.g.footage
    spaces = f.video_gen_spaces or list(DEFAULT_VIDEO_SPACES)
    poll_model = f.pollinations_model
    if ai_model:
        spaces = VIDEO_MODELS.get(ai_model, spaces)
        poll_model = PHOTO_MODELS.get(ai_model, poll_model)
    return GenParams(
        width=ctx.g.video.width,
        height=ctx.g.video.height,
        pollinations_model=poll_model,
        video_spaces=spaces,
        style_suffix=f.gen_style_suffix,
        hf_token=os.environ.get("HF_TOKEN") or None,
        pollinations_token=os.environ.get("POLLINATIONS_TOKEN") or None,
    )


def _remote(providers: list[str]) -> list[str]:
    """Drop the `local` provider so a stock_* source actually hits the internet
    instead of silently falling back to assets/ on a missing API key."""
    return [p for p in providers if p != "local"]


def _providers_for(source: str, cfg_providers: list[str]) -> list[str]:
    """Provider chain for a source. ai_video/ai_photo put their generator first
    (wan / pollinations) and keep the configured stock chain as a fallback, since
    free AI generation — video especially — can be unavailable at run time."""
    base = _remote(cfg_providers)
    if source == "ai_video":
        lead = ["wan"]
    elif source == "ai_photo":
        lead = ["pollinations"]
    else:
        return base
    return lead + [p for p in base if p not in lead]


def _pick_continuous_clip(vis: VisualsConfig, ctx: AppContext, dirs: dict) -> Path:
    """Choose the single clip that plays behind the whole video (continuous mode)."""
    bg = vis.background
    if bg.source == "local_video":
        clip = _local(bg.assets_dir, ctx.used_clips)
        if not clip:
            raise FootageError(f"no local clips in {bg.assets_dir}")
        return clip
    # stock_video: one neutral clip, internet only (no local fallback)
    return find_clip(
        ctx.content.fallback_keywords, ctx.content.fallback_keywords,
        _remote(ctx.g.footage.providers),
        dirs["clip_cache"], dirs["footage"], ctx.used_clips, _gen(ctx),
    )


def _fill_background(scene: Scene, vis: VisualsConfig, ctx: AppContext, dirs: dict, cont: dict | None) -> None:
    bg = vis.background
    providers = _providers_for(bg.source, ctx.g.footage.providers)
    gen = _gen(ctx, bg.ai_model)

    # continuous video background: reuse the one clip, advance the read offset.
    if cont is not None:
        scene.bg_assets = [BgAsset(
            path=cont["clip"], duration=scene.duration, is_photo=False, start=cont["offset"],
        )]
        cont["offset"] += scene.duration
        return

    # video background: stock search, local dir, or AI-generated per scene
    if bg.source in ("stock_video", "local_video", "ai_video"):
        if bg.source == "local_video":
            clip = _local(bg.assets_dir, ctx.used_clips)
            if not clip:
                raise FootageError(f"no local clips in {bg.assets_dir}")
        else:
            keywords = scene.keywords if bg.linkage == "narration" else []
            clip = find_clip(
                keywords, ctx.content.fallback_keywords, providers,
                dirs["clip_cache"], dirs["footage"], ctx.used_clips, gen,
            )
        scene.bg_assets = [BgAsset(path=clip, duration=scene.duration, is_photo=False)]
        return

    # photo background: slice the scene into interval_s pieces
    n = max(1, round(scene.duration / bg.interval_s))
    slice_dur = scene.duration / n
    queries = _queries(scene, n, ctx.content.fallback_keywords)
    assets = []
    for i in range(n):
        if bg.source == "local_photo":
            img = _local(bg.assets_dir, ctx.used_clips, IMAGE_EXTS)
            if not img:
                raise FootageError(f"no local images in {bg.assets_dir}")
        else:
            q = queries[i] if bg.linkage == "narration" else random.choice(
                ctx.content.fallback_keywords or [queries[i]]
            )
            img = find_image(
                q, ctx.content.fallback_keywords, providers,
                dirs["img_cache"], dirs["images"], ctx.used_clips, gen,
            )
        assets.append(BgAsset(path=img, duration=slice_dur, is_photo=True))
    scene.bg_assets = assets


def _fetch_insert(query: str, fg, ctx: AppContext, dirs: dict) -> Path:
    providers = _providers_for(fg.source, ctx.g.footage.providers)
    gen = _gen(ctx, fg.ai_model)
    if fg.source == "local_photo":
        path = _local(fg.assets_dir, ctx.used_clips, IMAGE_EXTS)
        if not path:
            raise FootageError(f"no local images in {fg.assets_dir}")
    elif fg.source == "local_video":
        path = _local(fg.assets_dir, ctx.used_clips, VIDEO_EXTS)
        if not path:
            raise FootageError(f"no local clips in {fg.assets_dir}")
    elif fg.source in ("stock_video", "ai_video"):
        path = find_clip(
            [query], ctx.content.fallback_keywords, providers,
            dirs["clip_cache"], dirs["footage"], ctx.used_clips, gen,
        )
    else:  # stock_photo or ai_photo
        path = find_image(
            query, ctx.content.fallback_keywords, providers,
            dirs["img_cache"], dirs["images"], ctx.used_clips, gen,
        )
    return path


def _fill_foreground(
    scene: Scene, vis: VisualsConfig, ctx: AppContext, dirs: dict, scene_start: float,
    idx: int = 0, delivered: dict[str, Path] | None = None,
) -> None:
    """Place foreground inserts driven by the narration: each LLM cue is anchored
    to the exact words of its phrase and shown only while those words are spoken.
    When ``delivered`` is given the fg source is user-assisted: each anchored cue's
    clip is looked up by id (``fg_<scene>_<cue>``) instead of being fetched."""
    fg = vis.foreground
    if not fg.enabled or not scene.insert_cues:
        return
    is_video = fg.source in ("stock_video", "local_video", "ai_video")

    inserts: list[FgInsert] = []
    for k, cue in enumerate(scene.insert_cues):
        span = _anchor_phrase(cue.phrase, scene.words, scene_start, scene.duration)
        if span is None:  # phrase not found in the audio — skip rather than guess
            continue
        start, end = span
        if delivered is not None:
            path = delivered.get(f"fg_{idx:02d}_{k}")
            if path is None:  # not delivered (cue anchored differently) — skip
                continue
        else:
            path = _fetch_insert(cue.query or " ".join(scene.keywords), fg, ctx, dirs)
        inserts.append(FgInsert(path=path, start=start, duration=end - start, is_video=is_video))

    # keep inserts from stacking: clip each end to the next start
    inserts.sort(key=lambda x: x.start)
    for a, b in zip(inserts, inserts[1:]):
        if a.start + a.duration > b.start:
            a.duration = max(b.start - a.start, 0.5)
    scene.fg_inserts = inserts


def _collect_manual(job: VideoJob, ctx: AppContext, manual_bg: bool, manual_fg: bool) -> dict[str, Path]:
    """Register a manual shot for every user-assisted asset — one background per
    non-ad scene (id ``shot_NN``) and/or one per anchored foreground cue (id
    ``fg_NN_K``) — and gather the operator's clips. Prompts are narration-derived
    (info mode has no authored video_prompt). Raises ManualInputPending until every
    clip is in; the assemble stage loops/crops each to its on-screen duration."""
    suffix = ctx.g.footage.gen_style_suffix
    fallback = ctx.content.fallback_keywords

    def _prompt(q: str) -> str:
        return ", ".join(p for p in (q, suffix) if p.strip()) or "cinematic scene"

    specs: list[manual.ShotSpec] = []
    scene_start = 0.0  # must mirror run()'s offset so fg anchoring lines up
    for i, scene in enumerate(job.scenes):
        if scene.is_ad:
            scene_start += scene.duration
            continue
        if manual_bg:
            specs.append(manual.ShotSpec(
                id=f"shot_{i:02d}", scene_index=i,
                prompt=_prompt(_queries(scene, 1, fallback)[0]), target_s=scene.duration,
            ))
        if manual_fg:
            for k, cue in enumerate(scene.insert_cues):
                span = _anchor_phrase(cue.phrase, scene.words, scene_start, scene.duration)
                if span is None:
                    continue
                specs.append(manual.ShotSpec(
                    id=f"fg_{i:02d}_{k}", scene_index=i,
                    prompt=_prompt(cue.query or " ".join(scene.keywords)),
                    target_s=span[1] - span[0],
                ))
        scene_start += scene.duration
    if not specs:
        return {}
    return manual.collect_or_pause(job.workdir, specs, ctx.g.video.width, ctx.g.video.height)


def run(job: VideoJob, ctx: AppContext) -> None:
    vis = ctx.visuals
    bg, fg = vis.background, vis.foreground
    dirs = {
        "clip_cache": ctx.g.paths.state / "cache" / "footage",
        "img_cache": ctx.g.paths.state / "cache" / "images",
        "footage": ctx.g.paths.assets / "footage",
        "images": ctx.g.paths.assets / "images",
    }
    # user-assisted background/foreground: gather hand-made clips first (raises → `paused`).
    manual_bg = bg.source == "ai_video" and bg.ai_model == "manual"
    manual_fg = fg.enabled and fg.source == "ai_video" and fg.ai_model == "manual"
    delivered = _collect_manual(job, ctx, manual_bg, manual_fg) if (manual_bg or manual_fg) else {}

    # continuous background: pick the single clip once, then track a running
    # read offset across scenes so the loop plays straight through.
    cont: dict | None = None
    if bg.continuous and bg.source in ("stock_video", "local_video"):
        cont = {"clip": _pick_continuous_clip(vis, ctx, dirs), "offset": 0.0}

    scene_start = 0.0  # running absolute offset, to anchor phrase-timed inserts
    for i, scene in enumerate(job.scenes):
        if scene.is_ad:
            ad_dir = ctx.ad.native.assets_dir
            clips = (
                [p for p in ad_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS]
                if ad_dir.is_dir()
                else []
            )
            if not clips:
                raise FootageError(f"no native ad clips in {ad_dir}")
            scene.clip = random.choice(clips)
            scene.bg_assets = [BgAsset(path=scene.clip, duration=scene.duration, is_photo=False)]
            if cont is not None:  # keep the gameplay timeline advancing past the ad
                cont["offset"] += scene.duration
            scene_start += scene.duration
            continue
        bg_id = f"shot_{i:02d}"
        if manual_bg and bg_id in delivered:  # manual background clip
            scene.bg_assets = [BgAsset(path=delivered[bg_id], duration=scene.duration, is_photo=False)]
        else:
            _fill_background(scene, vis, ctx, dirs, cont)
        _fill_foreground(scene, vis, ctx, dirs, scene_start, i, delivered if manual_fg else None)
        scene_start += scene.duration
