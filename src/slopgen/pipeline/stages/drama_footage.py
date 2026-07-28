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
import math
import random
import re
from pathlib import Path

from ...media.ffmpeg import duration_of
from ...media.generate import (
    DEFAULT_VIDEO_SPACES,
    PHOTO_MODELS,
    VIDEO_MODELS,
    GenParams,
    env_keys,
    is_manual_model,
    is_video_model,
    key_var_for_model,
    pollinations_image,
    wan_video,
)
from ...media.stock import VIDEO_EXTS, FootageError, find_image
from .. import manual
from ..context import AppContext
from ..job import BgAsset, VideoJob, Word

log = logging.getLogger(__name__)

# How far each medium may be retimed to meet the other. Speech degrades audibly
# well before picture does — a voice 30% off still reads as the same person, while
# a clip at half or double speed just reads as slow-motion or haste — so the video
# band is the wider one. Past both bands the picture is looped/trimmed as a last resort.
TEMPO_LO, TEMPO_HI = 0.8, 1.35   # voice (atempo)
VIDEO_LO, VIDEO_HI = 0.5, 2.0    # picture (setpts)

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


_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
# shot-list phrasing a video generator turns into a split-screen storyboard
_CUT_LIST = re.compile(r"\bTHEN\b|\bcut to\b|\bsplit[- ]screen\b|\bmontage\b|\bstoryboard\b", re.I)

# Closing clause on every shot prompt. Generators reach for a storyboard layout on
# their own — even a single-action prompt has come back as a grid of panels — and
# spelling out the negative is what they respond to.
SINGLE_FRAME = "single continuous shot, one full-frame image, no split screen, no panels, no grid, no collage"


def _short_tag(look: str) -> str:
    """The first few descriptor tokens — enough to re-identify a character on a
    repeat mention without pasting the whole sheet in again."""
    return ", ".join(t.strip() for t in look.split(",")[:3] if t.strip())


def _drop_foreign(text: str) -> str:
    """Remove any word still carrying non-Latin letters. Generators render such
    words as literal captions burned into the frame (observed: Cyrillic character
    names printed across the shot), so nothing but English may reach them."""
    if not _CYRILLIC.search(text):
        return text
    kept = [w for w in text.split() if not _CYRILLIC.search(w)]
    return " ".join(kept)


def _shot_prompt(scene, cast_prompts: dict[str, str], notes: str = "") -> str:
    """Compose the generator prompt: the shot description with every character's
    compiled look substituted IN PLACE of their name.

    Names never survive into the prompt. An image model cannot map "Юки" to a face,
    and a foreign name is rendered as literal on-screen text; worse, prepending all
    the looks as one comma bag leaves the model to guess which description belongs
    to whom, which is how two characters get blended or swapped between shots.
    Substituting each look where the name stands binds the description to the person
    actually doing the action. Characters present but never named in the description
    are appended at the end, and a repeat mention gets a short tag instead of the
    whole sheet."""
    text = scene.video_prompt or ""
    mentioned: list[str] = []
    # longest name first so "Сергей Костенко" is not eaten by "Сергей"
    for name in sorted(cast_prompts, key=len, reverse=True):
        look = cast_prompts.get(name, "").strip()
        if not look or not name.strip():
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if not pattern.search(text):
            continue
        # first mention carries the full look, later ones a short tag
        text = pattern.sub(lambda _m: f"({look})", text, count=1)
        text = pattern.sub(lambda _m: f"({_short_tag(look)})", text)
        mentioned.append(name)

    absent = [
        cast_prompts[n] for n in scene.characters
        if cast_prompts.get(n) and n not in mentioned
    ]
    # the run's visual constraints ride along on every prompt, so a hand-edited or
    # AI-rewritten one cannot quietly drop them. Non-Latin words are stripped below,
    # so a constraint written in Russian only reaches the generator via the writer.
    parts = ([text] if text.strip() else []) + absent + ([notes.strip()] if notes.strip() else []) + [SINGLE_FRAME]
    return _drop_foreign(", ".join(p for p in parts if p.strip()))


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
    if is_manual_model(model):  # manual scenes are ingested in run(), never generated
        raise FootageError("manual scene reached the auto generator — this is a bug")
    prompt = (_shot_prompt(scene, cast_prompts, ctx.params.visual_notes)
              or " ".join(scene.characters) or "cinematic scene")
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
    """Make the clip and its voiceover exactly as long as each other.

    The mismatch is split between the two rather than dumped on either: the voice
    speeds up (or slows) by √ratio and the clip is retimed by the same factor the
    other way, so a 20-second line over a 15-second clip becomes 17.3 seconds of
    both — a 15% faster voice and a 13% slower clip, neither conspicuous. Loading
    it all onto the voice is what used to leave the clip short, and a short clip
    was looped: it restarted from the beginning mid-scene, in plain view.

    Each factor has its own limits (speech tolerates less retiming than picture),
    so an extreme ratio still leaves a remainder; the voice always plays whole and
    the picture takes the rest (looping only for what is genuinely left over).
    Stills simply span the narration — a photo has no length of its own."""
    natural = scene.audio_src_duration or source_len or scene.clip_target_s or 1.0
    if is_photo:
        scene.audio_tempo = 1.0
        scene.video_tempo = 1.0
        scene.duration = natural
        return
    clip = source_len or scene.clip_target_s or natural
    ratio = natural / clip if clip > 0 else 1.0
    # split evenly in log space, then clamp each to what its medium can take
    audio = min(max(math.sqrt(ratio), TEMPO_LO), TEMPO_HI)
    video = min(max(audio / ratio, VIDEO_LO), VIDEO_HI)
    # whatever the picture could not absorb goes back to the voice, within its own band
    audio = min(max(video * ratio, TEMPO_LO), TEMPO_HI)
    scene.audio_tempo = audio
    scene.video_tempo = video
    scene.duration = natural / audio


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


def _collect_manual(job: VideoJob, ctx: AppContext) -> dict[int, Path]:
    """Register a manual shot per user-assisted scene and gather the operator's
    clips. Returns {scene_index: clip} once all are delivered; otherwise raises
    ManualInputPending so the orchestrator parks the job as `paused`."""
    manual_idx = [
        i for i, scene in enumerate(job.scenes)
        if not scene.is_ad and is_manual_model(scene.gen_model or "wan2.1")
    ]
    if not manual_idx:
        return {}
    specs = [
        manual.ShotSpec(
            id=f"shot_{i:02d}",
            scene_index=i,
            prompt=_shot_prompt(job.scenes[i], job.cast_prompts, ctx.params.visual_notes)
            or " ".join(job.scenes[i].characters)
            or "cinematic scene",
            target_s=job.scenes[i].clip_target_s,
            part=job.scenes[i].part,
        )
        for i in manual_idx
    ]
    idmap = manual.collect_or_pause(job.workdir, specs, ctx.g.video.width, ctx.g.video.height)
    return {i: idmap[f"shot_{i:02d}"] for i in manual_idx}


def run(job: VideoJob, ctx: AppContext) -> None:
    dirs = {
        "clip_cache": ctx.g.paths.state / "cache" / "footage",
        "img_cache": ctx.g.paths.state / "cache" / "images",
        "footage": ctx.g.paths.assets / "footage",
        "images": ctx.g.paths.assets / "images",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    # user-assisted scenes: gather their hand-made clips first. This raises
    # ManualInputPending (→ a clean `paused` checkpoint) until every one is in.
    delivered = _collect_manual(job, ctx)

    # A shot description that reads as a cut list makes generators render every shot
    # at once (a split-screen storyboard) before playing the sequence. The writer is
    # told not to, but it can slip through — flag it so the operator can fix the
    # prompts at the `script` breakpoint instead of wondering at the result.
    cut_listed = [i for i, s in enumerate(job.scenes) if _CUT_LIST.search(s.video_prompt or "")]
    if cut_listed:
        log.warning(
            "%d shot prompt(s) describe several shots in one clip (scenes %s). Generators "
            "render that as all shots on screen simultaneously — rewrite them as ONE "
            "continuous take (the `script` breakpoint lets you edit them before generation).",
            len(cut_listed), ", ".join(str(i) for i in cut_listed),
        )

    cursors: dict[str, int] = {}  # rotating key index, shared across scenes
    want_video = fell_back = 0
    for i, scene in enumerate(job.scenes):
        if scene.is_ad:
            clip, is_photo, source_len = _ad_clip(scene, ctx)
            scene.clip = clip
        elif i in delivered:  # manual clip supplied by the operator
            clip, is_photo, source_len = delivered[i], False, duration_of(delivered[i])
        else:
            if is_video_model(scene.gen_model or "wan2.1"):
                want_video += 1
            clip, is_photo, source_len = _generate(scene, ctx, dirs, cursors, job.cast_prompts)
            if is_photo and is_video_model(scene.gen_model or "wan2.1"):
                fell_back += 1
        _sync(scene, source_len, is_photo)
        scene.bg_assets = [BgAsset(path=clip, duration=scene.duration, is_photo=is_photo,
                                   speed=scene.video_tempo)]
        ctx.progress("footage", i + 1, len(job.scenes))

    if want_video and fell_back:
        level = log.error if fell_back == want_video else log.warning
        level(
            "AI video: %d/%d scenes fell back to stills (video Spaces unavailable or "
            "quota exhausted). The result will be a slideshow for those scenes.",
            fell_back, want_video,
        )

    _rebuild_words(job)
