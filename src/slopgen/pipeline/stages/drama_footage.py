"""Drama stage 3: generate one AI shot per scene and sync the voiceover to it.

Each non-ad scene is rendered by the generator the orchestration pinned to it
(see pipeline/drama.py). The prompt is the scene's English ``video_prompt`` with
the compiled visual prompt of every character present prepended, so faces/outfits
stay on-model. API keys are consumed per the stage's ``key_mode`` — ``rotate``
walks every key on a limit, ``single`` uses one and then falls back. If every key
and Space fails, the scene falls back to a stock image so the run still completes.

The clip length is authoritative: the scene's narration (already synthesized in
the tts stage, stored scene-relative) is time-stretched with atempo to fit, and
the word timings are rebuilt into absolute, stretched positions for subtitles.
"""

from __future__ import annotations

import logging
import random

from ...media.ffmpeg import duration_of
from ...media.generate import (
    DEFAULT_VIDEO_SPACES,
    PHOTO_MODELS,
    VIDEO_MODELS,
    GenParams,
    env_keys,
    is_video_model,
    key_var_for_model,
    pollinations_image,
    wan_video,
)
from ...media.stock import VIDEO_EXTS, FootageError, find_image
from ..context import AppContext
from ..job import BgAsset, VideoJob, Word

log = logging.getLogger(__name__)

# keep the atempo stretch modest so the voice never sounds sped-up/chipmunked;
# outside this band the clip is looped/trimmed to the (mildly) stretched voice.
TEMPO_LO, TEMPO_HI = 0.75, 1.6

# generic stock queries for the last-ditch fallback (drama has no content-type
# fallback_keywords to borrow, and stock APIs are English-indexed).
_FALLBACK_KEYWORDS = ["anime scene", "cinematic portrait", "dramatic lighting"]


def _genparams(ctx: AppContext, model: str, token: str | None) -> GenParams:
    f = ctx.g.footage
    if is_video_model(model):
        spaces = VIDEO_MODELS.get(model) or f.video_gen_spaces or list(DEFAULT_VIDEO_SPACES)
        return GenParams(
            width=ctx.g.video.width, height=ctx.g.video.height,
            video_spaces=spaces, style_suffix=f.gen_style_suffix, hf_token=token,
        )
    return GenParams(
        width=ctx.g.video.width, height=ctx.g.video.height,
        pollinations_model=PHOTO_MODELS.get(model, f.pollinations_model),
        style_suffix=f.gen_style_suffix, pollinations_token=token,
    )


def _shot_prompt(scene, cast_prompts: dict[str, str]) -> str:
    """Compose the generator prompt: present characters' looks + the shot."""
    looks = [cast_prompts[n] for n in scene.characters if cast_prompts.get(n)]
    parts = looks + ([scene.video_prompt] if scene.video_prompt else [])
    return ", ".join(p for p in parts if p.strip())


def _key_candidates(scene, keys: list[str], cursors: dict[str, int]) -> list[str | None]:
    """Ordered API keys to try for this scene, per its key_mode. Empty key list →
    a single keyless attempt (pollinations needs none; HF token only speeds wan)."""
    if not keys:
        return [None]
    if scene.key_mode == "single":
        try:
            idx = int(scene.key) if scene.key != "" else 0
        except ValueError:
            idx = 0
        return [keys[idx % len(keys)]]
    # rotate: start at the running cursor and walk every key once
    var_i = cursors.get("i", 0)
    ordered = [keys[(var_i + n) % len(keys)] for n in range(len(keys))]
    cursors["i"] = (var_i + 1) % len(keys)  # next scene starts on the next key
    return ordered


def _generate(scene, ctx: AppContext, dirs: dict, cursors: dict, cast_prompts: dict):
    """Return (path, is_photo, source_len_s) for the scene's shot, or raise."""
    model = scene.gen_model or "wan2.1"
    prompt = _shot_prompt(scene, cast_prompts) or " ".join(scene.characters) or "cinematic scene"
    keys = env_keys(key_var_for_model(model))
    video = is_video_model(model)
    cache = dirs["clip_cache"] if video else dirs["img_cache"]

    for token in _key_candidates(scene, keys, cursors):
        gen = _genparams(ctx, model, token)
        try:
            path = wan_video(prompt, cache, ctx.used_clips, gen) if video \
                else pollinations_image(prompt, cache, ctx.used_clips, gen)
        except Exception:
            path = None
        if path:
            return path, (not video), (duration_of(path) if video else scene.clip_target_s)

    # every key/Space failed — fall back to a stock still so the run survives
    if video:
        log.warning(
            "video generation failed for a %s scene (all keys/Spaces exhausted) — "
            "falling back to a still image", model,
        )
    img = find_image(
        prompt, _FALLBACK_KEYWORDS, [p for p in ctx.g.footage.providers if p != "local"],
        dirs["img_cache"], dirs["images"], ctx.used_clips, _genparams(ctx, "flux", None),
    )
    return img, True, scene.clip_target_s


def _ad_clip(scene, ctx: AppContext):
    ad_dir = ctx.ad.native.assets_dir
    clips = [p for p in ad_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS] if ad_dir.is_dir() else []
    if not clips:
        raise FootageError(f"no native ad clips in {ad_dir}")
    clip = random.choice(clips)
    return clip, False, duration_of(clip)


def _sync(scene, source_len: float, is_photo: bool) -> None:
    """Set the scene's final duration and atempo factor from the natural voice
    length vs. the shot length. Stills simply span the narration (no stretch);
    video clips are the master and the voice is stretched (within limits) to them."""
    natural = scene.audio_src_duration or source_len or scene.clip_target_s or 1.0
    if is_photo:
        scene.audio_tempo = 1.0
        scene.duration = natural
        return
    target = source_len or scene.clip_target_s or natural
    tempo = min(max(natural / target, TEMPO_LO), TEMPO_HI)
    scene.audio_tempo = tempo
    scene.duration = natural / tempo


def _rebuild_words(job: VideoJob) -> None:
    """Turn each scene's scene-relative word timings into absolute, stretched
    positions on the final timeline (drama tts stores them scene-relative)."""
    offset = 0.0
    for scene in job.scenes:
        factor = (scene.duration / scene.audio_src_duration) if scene.audio_src_duration else 1.0
        scene.words = [
            Word(text=w.text, start=offset + w.start * factor, end=offset + w.end * factor)
            for w in scene.words
        ]
        offset += scene.duration


def run(job: VideoJob, ctx: AppContext) -> None:
    dirs = {
        "clip_cache": ctx.g.paths.state / "cache" / "footage",
        "img_cache": ctx.g.paths.state / "cache" / "images",
        "footage": ctx.g.paths.assets / "footage",
        "images": ctx.g.paths.assets / "images",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    cursors: dict[str, int] = {}  # rotating key index, shared across scenes
    want_video = fell_back = 0
    for scene in job.scenes:
        if scene.is_ad:
            clip, is_photo, source_len = _ad_clip(scene, ctx)
            scene.clip = clip
        else:
            if is_video_model(scene.gen_model or "wan2.1"):
                want_video += 1
            clip, is_photo, source_len = _generate(scene, ctx, dirs, cursors, job.cast_prompts)
            if is_photo and is_video_model(scene.gen_model or "wan2.1"):
                fell_back += 1
        _sync(scene, source_len, is_photo)
        scene.bg_assets = [BgAsset(path=clip, duration=scene.duration, is_photo=is_photo)]

    if want_video and fell_back:
        level = log.error if fell_back == want_video else log.warning
        level(
            "AI video: %d/%d scenes fell back to stills (video Spaces unavailable or "
            "quota exhausted). The result will be a slideshow for those scenes.",
            fell_back, want_video,
        )

    _rebuild_words(job)
