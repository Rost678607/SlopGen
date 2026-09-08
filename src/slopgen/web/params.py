"""What a form MEANS, per mode.

One function per mode, and every door on top of them is three lines. They were inline
in the browser's routes until a loop could be retuned — the browser edits a loop's
settings in the same form that starts a run, so "what this form means" had to become
something two callers can ask rather than something one of them does. The chat is the
third caller, and the reason this is a module: a bot that built its own `RunParams`
would be a second, quietly diverging answer to the same question, and the divergence
would show up as a run that behaves differently depending on which button started it.

The body they read is the browser's JSON, and the chat assembles the same dict out of
its buttons. `HTTPException` is what a bad one raises — it carries a `detail` worth
showing to a person, and a caller that is not HTTP is free to catch it and print that.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..config import ConfigStore, RunParams
from ..config.models import OrchestrationConfig, OrchestrationStage
from ..media.filters import CATALOGUE as FILTER_CATALOGUE
from ..media.generate import PHOTO_MODELS, VIDEO_MODELS, model_clip_seconds

# The montage effects, as {key: what it does}. Read off the filter catalogue rather
# than listed here, so an effect added there is accepted on its own.
FILTER_HELP = {e.key: e.note for e in FILTER_CATALOGUE}


def common(b: dict) -> dict:
    """The settings every mode shares, read off one block rather than three.

    They were missing from the browser entirely, and `filters` is the one that
    mattered most: the montage look — grain, tape, tube, glitch — is most of how
    this genre reads, and it is the only picture control that works in every mode
    and from every source, because it is asked of ffmpeg rather than of a model."""
    out: dict = {
        "profanity": int(b.get("profanity", 0)),
        "ad": str(b.get("ad", "")),
        "ad_mode": b.get("ad_mode", "both"),
        "push": str(b.get("push", "")),
        "visual_notes": str(b.get("visual_notes", "")),
        "visual_style": str(b.get("visual_style", "")),
        "clean_subtitles": bool(b.get("clean_subtitles", False)),
        "voice_override": str(b.get("voice_override", "")),
        "tts_engine": str(b.get("tts_engine", "")),
        "tts_rate": int(b.get("tts_rate", 0)),
        "keep_temp": bool(b.get("keep_temp", False)),
        "filters": {k: max(0, min(100, int(v)))
                    for k, v in (b.get("filters") or {}).items()
                    if k in FILTER_HELP and int(v) > 0},
    }
    if b.get("subtitle_style"):
        out["subtitle_style"] = b["subtitle_style"]
    return out


def fandom_params(store: ConfigStore, b: dict) -> RunParams:
    """A fandom run, from the form's body.

    The chain is built here rather than named, because a fandom's picture comes
    from ONE source for the whole video — `frames` most of all, which is
    all-or-nothing by construction (see framebase.active). So the form picks the
    source and this turns it into a one-stage chain, which is what the pipeline
    reads. Hardcoding `frames` was the first version, and it left the old
    per-shot modes unreachable from the browser entirely."""
    world = str(b.get("fandom", ""))
    if world not in store.fandoms:
        raise HTTPException(status_code=404, detail=f"no world named {world!r}")
    medium = b.get("medium", "photo")
    source = str(b.get("source") or ("frames" if medium == "photo" else "wan2.1"))
    allowed = (list(PHOTO_MODELS) + ["manual", "search"]) if medium == "photo" \
        else list(VIDEO_MODELS)
    if source not in allowed:
        raise HTTPException(status_code=422,
                            detail=f"{source!r} does not make {medium}")
    params = RunParams(
        lang=str(b.get("lang", "ru")), content_type="", mode="fandom",
        fandom=world, fandom_voice=b.get("voice", "resident"), medium=medium,
        scenario=str(b.get("scenario", "")),
        duration_s=float(b.get("duration_s", 45.0)),
        count=int(b.get("count", 1)),
        dry_run=bool(b.get("dry_run", True)),
        breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
        frame_fit=b.get("frame_fit", "close"),
        cut_sensitivity=float(b.get("cut_sensitivity", 0.35)),
        **common(b),
        manual_orchestration=OrchestrationConfig(
            name=source,
            stages=[OrchestrationStage(model=source, metric="percent", amount=100.0,
                                       clip_seconds=model_clip_seconds(source))]),
    )
    return params


def info_params(store: ConfigStore, b: dict) -> RunParams:
    """The minute-of-useless-info clip: a topic, or none and the model invents one.

    `store` is unused and kept anyway: one signature across the three is what lets a
    caller dispatch on the mode rather than write the same branch three times."""
    return RunParams(
        lang=str(b.get("lang", "ru")),
        content_type=str(b.get("content_type", "")),
        mode="info", idea=str(b.get("idea", "")),
        visuals=str(b.get("visuals", "classic")),
        duration_s=float(b.get("duration_s", 45.0)),
        count=int(b.get("count", 1)),
        dry_run=bool(b.get("dry_run", True)),
        breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
        **common(b),
    )


def drama_params(store: ConfigStore, b: dict) -> RunParams:
    """The AI drama: a premise, a cast, and a generator chain.

    The chain is the one thing this mode cannot default sensibly — it is what the
    operator is rationing free tiers with — so it is named, and an unknown name is
    refused here rather than silently falling back three stages later."""
    orch = str(b.get("orchestration", ""))
    if orch and orch not in store.orchestrations:
        raise HTTPException(status_code=404, detail=f"no orchestration {orch!r}")
    return RunParams(
        lang=str(b.get("lang", "ru")), content_type="", mode="drama",
        scenario=str(b.get("scenario", "")),
        # the cast is resolved to the full character cards here rather than passed
        # as names: `manual_cast` is what the pipeline reads, and a name it cannot
        # find would otherwise become a person with no face three stages later
        manual_cast=[store.characters[c] for c in b.get("cast", [])
                     if isinstance(c, str) and c in store.characters],
        orchestration=orch,
        duration_s=float(b.get("duration_s", 45.0)),
        parts=int(b.get("parts", 1)),
        count=int(b.get("count", 1)),
        dry_run=bool(b.get("dry_run", True)),
        breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
        duration_tol_s=float(b.get("duration_tol_s", 0.0)),
        parts_iterative=bool(b.get("parts_iterative", True)),
        clip_seconds=float(b.get("clip_seconds", 0.0)),
        **common(b),
    )


def loop_of(b: dict) -> dict | None:
    """The loop block a form may send, or None when it asked for a plain run.

    The three mode forms send the same block, and it is deliberately the ONLY
    difference between starting one video and starting a hundred: a loop is this run
    with its topic left open, so every other setting on the form means exactly what
    it meant before (see pipeline/loop.py)."""
    loop = b.get("loop")
    if not isinstance(loop, dict) or not loop.get("on"):
        return None
    return {
        "source": "me" if str(loop.get("source", "ai")) == "me" else "ai",
        "limit": max(0, int(loop.get("limit", 0) or 0)),
        "on_park": "go_on" if str(loop.get("on_park", "hold")) == "go_on" else "hold",
        "topics": [str(t).strip() for t in (loop.get("topics") or []) if str(t).strip()],
    }
