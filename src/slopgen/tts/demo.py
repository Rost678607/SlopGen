"""Speaking ONE line, to hear a voice before a whole video is committed to it.

Deliberately not a pipeline run. There is no job, no aligner pass for timings, no
subtitle track and no cache of takes: the only question a demo answers is "does this
voice sound right", and everything else is time spent before the answer arrives. That
matters most on the local model, where the line alone already costs a minute of CPU.

The one thing it does keep from the pipeline is the RESOLUTION rule (`voice_for`) —
a name that matches a card under `configs/voices/` is a clone, anything else is a
catalogue id, one namespace either way. Without that, what you hear here and what a
run with the same `--voice` says could differ, which would make the demo worthless
precisely when it matters.
"""

from __future__ import annotations

from pathlib import Path

from .base import TTSError, Voice, build

# What a demo says. Long enough to judge a voice on — it has to carry a question, a
# pause and a number, because a voice that reads a flat clause well can still fumble
# all three — and short enough that the local model, at five minutes of CPU per minute
# of speech, answers while you are still waiting for it.
DEMO_TEXT = {
    "ru": "Марта закрыла дверь и обернулась. «Ты правда думаешь, что это закончится хорошо?» "
          "Их было двадцать семь, а осталось трое.",
    "en": "Martha closed the door and turned around. \"Do you really think this ends well?\" "
          "There were twenty-seven of them, and three are left.",
}

# A local take is a dice roll, not a deterministic result: the same line comes back
# usable or as a stall depending on the sampling. The pipeline re-rolls for exactly
# this reason, and a demo that gave up after one throw would show the operator a
# failure the pipeline itself would have shrugged off. A catalogue engine has no dice,
# so it gets one throw.
ATTEMPTS = 3


def voice_for(store, name: str, lang: str) -> Voice:
    """A menu entry turned into something an engine can speak with, by the SAME rule
    the pipeline uses (`pipeline/stages/tts._resolve_voice`)."""
    card = store.voices.get(name)
    if card is None:
        return Voice(name=name, lang=lang)
    ref = card.ref_path
    return Voice(name=name, lang=card.lang or lang,
                 ref_audio=Path(ref) if ref else None,
                 ref_text=card.text, ref_url=card.ref_url)


def clipper(store, lang: str):
    """A function that cuts whatever a cloned take says outside the line, or None when
    the recogniser it needs is not installed — a demo is worth hearing even then, so
    this degrades rather than refuses."""
    from ..media.ffmpeg import duration_of
    from ..models import ModelStore
    from . import align as aligner

    try:
        model_dir = ModelStore(store.global_cfg.paths.models).require(
            aligner.model_for(lang, store.global_cfg.tts))
    except Exception:  # noqa: BLE001 — no recogniser, no clipping
        return None

    def _clip(engine, path: Path, text: str, voice: Voice) -> None:
        from . import verify_take

        _words, seconds, matched = aligner.clip_to_script(
            path, text, model_dir, duration_of(path))
        verify_take(engine, text, voice, seconds, matched)

    return _clip


def speak(store, engine_id: str, voice_name: str, lang: str, text: str,
          out_dir: Path) -> Path:
    """Say one line and return the file it landed in. Raises on failure, with the last
    engine complaint as the message — a caller with nothing to play needs the reason,
    not a silent empty result."""
    eng = build(engine_id, store.global_cfg.tts, lang, store.global_cfg.paths.models)
    voice = voice_for(store, voice_name, lang)
    out = out_dir / f"demo{eng.suffix}"
    clones = getattr(eng, "clones", False)
    clip = clipper(store, lang) if clones else None
    last = ""
    for _ in range(ATTEMPTS if clones else 1):
        try:
            eng.synthesize(text, voice, "+0%", out)
            if clip is not None:
                clip(eng, out, text, voice)
        except TTSError as e:  # a rejected take — throw again
            last = str(e)
            continue
        return out
    raise RuntimeError(last or "the engine returned nothing usable")
