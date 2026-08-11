"""Drama stage 4: generate one AI shot per scene and sync the voiceover to it.

Each non-ad scene is rendered by the generator the orchestration pinned to it
(see pipeline/drama.py). The prompt is the scene's English ``video_prompt`` with
the compiled look of every character AND every registered entity (stages/entities.py)
substituted in place of its name, so faces, outfits and recurring things stay
on-model. API keys are consumed per the stage's ``key_mode`` — ``rotate``
walks every key on a limit, ``single`` uses one and then falls back. If every key
and Space fails, the scene falls back to a stock image so the run still completes.

The clip length is authoritative: the scene's narration (already synthesized in
the tts stage, stored scene-relative) is time-stretched with atempo to fit, and
the word timings are rebuilt into absolute, stretched positions for subtitles.
"""

from __future__ import annotations

import logging
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

# How far each medium may be retimed to meet the other, and in which order. The
# voice moves first: nobody sees it happen. The picture is only retimed once the
# voice has spent its comfortable range, and the bands differ by direction because
# the artefacts do:
#   voice faster  — stays natural well past +25%; the workhorse of the two
#   voice slower  — sounds sluggish almost immediately, so barely used
#   picture slower— reads as deliberate slow motion, very forgiving
#   picture faster— reads as comic haste, so never used: surplus picture is trimmed
AUDIO_COMFORT_UP = 1.25    # voice speed-up used before the picture is touched at all
AUDIO_HARD_UP = 1.45       # …and once the picture has nothing left to give
AUDIO_COMFORT_DOWN = 0.92  # voice slow-down: only to close a gap it can close outright
VIDEO_SLOW_MAX = 0.45      # picture at 45% speed is still watchable slow motion

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


# The appearance budget belongs to the FRAME, not to each person in it. Alone, a
# character can afford their whole sheet; three cannot. At ~20 tags each the looks are
# some 70% of the prompt and the ACTION — what the shot is actually of — is a rounding
# error, so the generator draws people matching their descriptions and has nothing left
# to spend on what they are doing. Observed: three fully-described people "fistfighting
# on the wing of a flying jet" came back as three people standing in a room.
#
# So a shared frame splits one budget: two people get 6 tags each, three get 4, four get
# the floor of 3. The floor is what keeps a face recognisable between shots (age, build,
# hair) — below it the cast stops being consistent, which is the problem the looks exist
# to solve. A single character is never trimmed, and neither is an entity: its descriptor
# is the only thing teaching the model what an invented compound like "robot-house" is.
CROWD_TAG_BUDGET = 12
MIN_TAGS = 3


def _tag_budget(named: int) -> int | None:
    """Tags per person for a frame holding `named` of them; None = the whole sheet."""
    return None if named <= 1 else max(CROWD_TAG_BUDGET // named, MIN_TAGS)

# "Игнат's robot-house" is a shot of the house; the possessive says who OWNS it, which
# is not a visual fact about the frame. Substituting the owner's appearance there is how
# a man with a tool belt gets glued onto a building.
_POSSESSIVE = "(?:'s|’s)\\s*"


def _tags(look: str, limit: int | None = None) -> str:
    """The look as descriptor tags, optionally capped to the first `limit` of them."""
    parts = [t.strip() for t in look.split(",") if t.strip()]
    return ", ".join(parts[:limit] if limit else parts)


def _short_tag(look: str) -> str:
    """The first few descriptor tokens — enough to re-identify a character on a
    repeat mention without pasting the whole sheet in again."""
    return _tags(look, 3)


def _mentions(text: str, name: str) -> bool:
    return bool(re.search(re.escape(name), text, re.IGNORECASE))


def _strip_possessives(text: str, names) -> str:
    """Drop ``Name's`` for every cast name that owns something in the shot, so the
    owner is not mistaken for somebody standing in it. A character who appears only
    possessively is not in frame at all and gets no look."""
    for name in sorted(names, key=len, reverse=True):
        if name.strip():
            text = re.sub(re.escape(name) + _POSSESSIVE, "", text, flags=re.IGNORECASE)
    return text


def _drop_foreign(text: str) -> str:
    """Remove any word still carrying non-Latin letters. Generators render such
    words as literal captions burned into the frame (observed: Cyrillic character
    names printed across the shot), so nothing but English may reach them."""
    if not _CYRILLIC.search(text):
        return text
    kept = [w for w in text.split() if not _CYRILLIC.search(w)]
    return " ".join(kept)


def _shot_prompt(
    scene, cast_prompts: dict[str, str], notes: str = "",
    entity_prompts: dict[str, str] | None = None,
) -> str:
    """Compose the generator prompt: the shot description with every character's and
    every registered entity's compiled look substituted IN PLACE of its name.

    Names never survive into the prompt. An image model cannot map "Юки" to a face,
    and a foreign name is rendered as literal on-screen text; worse, prepending all
    the looks as one comma bag leaves the model to guess which description belongs
    to whom, which is how two characters get blended or swapped between shots.
    Substituting each look where the name stands binds the description to the person
    or thing actually doing the action, and a repeat mention gets a short tag instead
    of the whole sheet.

    Two things keep the looks from swamping the shot. A frame holding several people
    splits one appearance budget between them (see :func:`_tag_budget`), because a
    prompt that is mostly appearance renders as a cast line-up rather than an action. And a
    character listed as present but never named contributes only a short tag: the
    whole sheet appended loose binds to nobody and reliably costs more than it buys.
    Registered entities are never trimmed — their descriptor is the only thing that
    says what an invented compound like "robot-house" looks like.

    Getting a name into the prompt in the first place is the ``entities`` stage's job
    (see stages/entities.py); this function only composes what it left behind."""
    entity_prompts = entity_prompts or {}
    raw = scene.video_prompt or ""
    text = _strip_possessives(raw, cast_prompts)
    # somebody the shot mentions ONLY as an owner ("Игнат's robot-house") is not in
    # the frame; the writer listing them as present is what would otherwise drag a
    # person into a shot of a building
    owners_only = {
        n for n in cast_prompts
        if n.strip() and _mentions(raw, n) and not _mentions(text, n)
    }
    # cast wins a name clash: a person the operator wrote down outranks a registry
    # entry that happens to share the spelling
    looks = {**entity_prompts, **cast_prompts}
    people = set(cast_prompts)
    named = [n for n in people if n.strip() and _mentions(text, n)]
    budget = _tag_budget(len(named))

    mentioned: list[str] = []
    # longest name first so "Сергей Костенко" is not eaten by "Сергей"
    for name in sorted(looks, key=len, reverse=True):
        look = looks.get(name, "").strip()
        if not look or not name.strip():
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if not pattern.search(text):
            continue
        first = _tags(look, budget) if name in people else look
        # first mention carries the (budgeted) look, later ones a short tag
        text = pattern.sub(lambda _m: f"({first})", text, count=1)
        text = pattern.sub(lambda _m: f"({_short_tag(look)})", text)
        mentioned.append(name)

    absent = [
        _short_tag(cast_prompts[n]) for n in scene.characters
        if cast_prompts.get(n) and n not in mentioned and n not in owners_only
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


def _generate(scene, ctx: AppContext, dirs: dict, cursors: dict, cast_prompts: dict,
              entity_prompts: dict):
    """Return (path, is_photo, source_len_s) for the scene's shot, or raise."""
    model = scene.gen_model or "wan2.1"
    if is_manual_model(model):  # manual scenes are ingested in run(), never generated
        raise FootageError("manual scene reached the auto generator — this is a bug")
    prompt = (_shot_prompt(scene, cast_prompts, ctx.params.visual_notes, entity_prompts)
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


def _clamp(value: float, band: tuple[float, float]) -> float:
    return min(max(value, band[0]), band[1])


def _fit(natural: float, clip: float) -> tuple[float, float]:
    """Work out (audio_tempo, video_tempo) that make a voice line and its clip the
    same length. Returns the factors; >1 plays faster.

    The voice is retimed first — it is the cheaper change, being invisible — and the
    picture is only touched once the voice has gone as far as it comfortably can:

      ratio ≤ ±COMFORT   the voice absorbs it alone, the clip plays untouched
      beyond that        the voice sits at its comfortable edge and the picture
                         covers the remainder
      beyond both        the voice is pushed to its hard limit before the picture
                         is asked for more, and whatever is still left over is
                         handled by trimming (clip long) or looping (clip short)

    The bands are asymmetric, because the two directions are not equally forgiving.
    Speech sped up stays natural far longer than speech slowed down, which quickly
    sounds drunk — so a voice shorter than its clip is barely stretched at all and
    the surplus picture is simply cut off, which costs nothing and shows nothing."""
    ratio = natural / clip if clip > 0 else 1.0
    if ratio >= 1.0:  # the voice is longer: speed it up, then slow the picture
        audio = _clamp(ratio, (1.0, AUDIO_COMFORT_UP))
        video = _clamp(audio / ratio, (VIDEO_SLOW_MAX, 1.0))
        if video * ratio > AUDIO_COMFORT_UP:  # picture maxed out — push the voice further
            audio = _clamp(video * ratio, (1.0, AUDIO_HARD_UP))
        else:
            audio = video * ratio
        return audio, video
    # The voice is SHORTER than the clip, and here the cheapest fix is no fix: the
    # segment simply ends with the speech and the surplus picture is cut off, which
    # costs one unused tail and shows nothing. Retiming to save that tail would mean
    # slowing the voice (sluggish) or racing the picture (comic) — and for a large
    # gap it would still trim afterwards, buying two artefacts and no benefit. So
    # only a gap small enough for a barely-audible stretch to close completely is
    # worth closing; anything wider is trimmed.
    if ratio >= AUDIO_COMFORT_DOWN:
        return ratio, 1.0  # the whole clip plays, the voice fills it exactly
    return 1.0, 1.0  # untouched; the clip is trimmed to the line


def _sync(scene, source_len: float, is_photo: bool) -> None:
    """Give the scene its final length, and the factors that get both media there.

    The clip length the operator authored is fixed, and what a line of speech
    actually takes to say is whatever edge-tts produced, so the two rarely agree.
    Reconciling them is :func:`_fit`'s job; the scene's duration then follows from
    the retimed voice, which always plays whole. Stills simply span the narration —
    a photo has no length of its own."""
    natural = scene.audio_src_duration or source_len or scene.clip_target_s or 1.0
    if is_photo:
        scene.audio_tempo = 1.0
        scene.video_tempo = 1.0
        scene.duration = natural
        return
    clip = source_len or scene.clip_target_s or natural
    audio, video = _fit(natural, clip)
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


def _entity_prompts(job: VideoJob) -> dict[str, str]:
    """name → look for every registered entity that actually carries a descriptor
    (the operator may have blanked one out at the `entities` breakpoint)."""
    return {e.name: e.visual_prompt for e in job.entities if e.name.strip() and e.visual_prompt.strip()}


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
    entity_prompts = _entity_prompts(job)
    specs = [
        manual.ShotSpec(
            id=f"shot_{i:02d}",
            scene_index=i,
            prompt=_shot_prompt(job.scenes[i], job.cast_prompts, ctx.params.visual_notes,
                                entity_prompts)
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
    entity_prompts = _entity_prompts(job)

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
            clip, is_photo, source_len = _generate(
                scene, ctx, dirs, cursors, job.cast_prompts, entity_prompts)
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
