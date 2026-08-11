"""Stage 3: synthesize each scene with edge-tts and capture word timings.

edge-tts streams WordBoundary events (offsets in 100ns ticks) alongside the
audio, which gives us subtitle timings for free — no Whisper needed.

Each line's raw (scene-relative) timings are cached next to its mp3 as
``scene_NN.json``, keyed by the exact text that produced them. A re-run — after a
crash, or after the operator edited the narration at the TTS breakpoint — then
re-synthesizes only the lines whose text actually changed and reuses the rest.

**Speed is per line, not per run.** The run's ``tts_rate`` is what every line is
voiced at by default, in both modes; a line the operator re-voiced at the breakpoint's
speed slider carries its own ``Scene.tts_rate`` and keeps it. Since the cache is keyed
on the rate as well, changing a line's speed re-synthesizes that line and nothing else.

**What is spoken is not always what is written.** A few words come out wrong no
matter how they are spelled in the script — a Cyrillic acronym whose letters form
a pronounceable syllable gets read as that syllable, so «НЛО» is said "нло"
instead of spelled out. The run's `pronounce` table (config `[tts.pronounce.<lang>]`)
respells those for the synthesizer only; the subtitles keep the original, because
the picture should read «НЛО». That mirrors what `--clean-subs` does in the other
direction, where the voice keeps every word and only the burned-in text changes.
The respelling is one token by construction (see `TTSConfig`), so the word timings
line up with the original and nothing has to be re-spread — the display text is
simply swapped back on the way out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path

import edge_tts

log = logging.getLogger(__name__)

from ...media.ffmpeg import duration_of
from ..context import AppContext
from ..job import VideoJob, Word

# Delays between retry attempts (seconds). Jitter of ±25% is applied at runtime
# to avoid thundering-herd when multiple scenes retry simultaneously in a batch.
_RETRY_DELAYS = (3.0, 8.0, 20.0, 35.0)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1

# fallback narrator voices when there is no content-type voice to borrow (drama
# mode) and the run didn't set an explicit voice_override.
_DEFAULT_VOICES = {"ru": "ru-RU-SvetlanaNeural", "en": "en-US-AriaNeural"}


def _resolve_voice(ctx: AppContext) -> str:
    if ctx.params.voice_override:
        return ctx.params.voice_override
    ct = ctx.params.content_type
    if ct and ct in ctx.store.content_types:
        v = ctx.content.voices.get(ctx.params.lang)
        if v:
            return v
    return _DEFAULT_VOICES.get(ctx.params.lang, "en-US-AriaNeural")


async def _synth(text: str, voice: str, out_path, rate: str = "+0%") -> list[dict]:
    words: list[dict] = []
    # edge-tts >= 7 defaults to SentenceBoundary; we need per-word timings
    com = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    with open(out_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 1e7,
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                })
    return words


def _boundaries(word: str) -> str:
    """Match `word` only as a whole word. `\\w` is Unicode-aware here, so this also
    keeps «ВОЗ» from firing inside «ВОЗДУХ»."""
    return rf"(?<!\w){re.escape(word)}(?!\w)"


def _spoken(text: str, table: dict[str, str]) -> str:
    """The line as the synthesizer must see it. Case-sensitive on purpose: «ВОЗ» is
    the acronym, «воз» in running text is a cart."""
    for written, said in table.items():
        if written.strip() and said.strip():
            text = re.sub(_boundaries(written), said, text)
    return text


_PUNCT = "«»\"'“”„.,!?;:—–-…()"


def _bare(token: str) -> str:
    return token.strip(_PUNCT).casefold()


def _tail(token: str) -> str:
    """The punctuation clinging to the end of a token, which the merged word keeps."""
    stripped = token.rstrip(_PUNCT)
    return token[len(stripped):]


def _as_written(raw: list[dict], table: dict[str, str]) -> list[dict]:
    """Undo :func:`_spoken` in the word timings, so subtitles show what the script
    wrote rather than the phonetic crutch fed to the voice.

    A respelling has to be several whitespace-separated tokens to be spelled out at
    all — measured in running speech, «эн эл о» takes 0.62s, while «эн-эл-о» takes
    0.26s, exactly as long as the broken «НЛО»: the normalizer collapses a hyphenated
    run back into one syllable. So the voice returns several
    WordBoundary events where the script had one word, and they are merged back into
    that word here. Because we know what was substituted, the merge is exact — the
    restored word spans from the first letter's start to the last one's end, and no
    timing is estimated (contrast :func:`~.subtitles._retime`, which has to re-spread
    a line whose new wording it cannot align)."""
    if not table:
        return raw
    # longest replacement first, so a three-token one is matched before any prefix of it
    subs = sorted(
        ((said.split(), written) for written, said in table.items() if said.split() and written.strip()),
        key=lambda s: -len(s[0]),
    )
    out: list[dict] = []
    i = 0
    while i < len(raw):
        for said_tokens, written in subs:
            window = raw[i:i + len(said_tokens)]
            if len(window) == len(said_tokens) and all(
                _bare(w["text"]) == _bare(t) for w, t in zip(window, said_tokens)
            ):
                out.append({
                    "text": written + _tail(window[-1]["text"]),
                    "start": window[0]["start"],
                    "end": window[-1]["end"],
                })
                i += len(said_tokens)
                break
        else:
            out.append(raw[i])
            i += 1
    return out


def _cache_path(audio: Path) -> Path:
    return audio.with_suffix(".json")


def _cached_words(audio: Path, text: str, voice: str, rate: str) -> list[dict] | None:
    """Timings from a previous run, but only if the audio next to them was made
    from exactly this text with the same voice and rate."""
    cache = _cache_path(audio)
    if not (audio.exists() and cache.exists()):
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("text") != text or data.get("voice") != voice or data.get("rate") != rate:
        return None
    words = data.get("words")
    return words if isinstance(words, list) and words else None


def _store_words(audio: Path, text: str, voice: str, rate: str, words: list[dict]) -> None:
    try:
        _cache_path(audio).write_text(
            json.dumps({"text": text, "voice": voice, "rate": rate, "words": words},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:  # a missing cache only costs a re-synthesis
        pass


def _pronounce(ctx: AppContext) -> dict[str, str]:
    """The run language's respelling table, or an empty one."""
    return ctx.g.tts.pronounce.get(ctx.params.lang, {})


def rate_str(percent: int) -> str:
    """A speech rate as edge-tts wants it: ``"+20%"``, ``"-15%"``, ``"+0%"``."""
    return f"{int(percent):+d}%"


def _scene_rate(scene, ctx: AppContext) -> int:
    """The percent THIS line is voiced at.

    A scene the operator re-voiced at another speed from the breakpoint carries its own
    rate; every other line takes the run's, so the stage's re-run reproduces the takes
    that were listened to rather than flattening them back to one speed.

    Both modes honour the run's rate. In drama it is not merely cosmetic: the
    scriptwriter sized every beat's narration to the words that fit one clip AT THIS
    SPEED (see `..drama.word_budget`), so the footage stage is left with only the
    residual mismatch to stretch away."""
    return int(scene.tts_rate) if scene.tts_rate is not None else int(ctx.params.tts_rate)


def _synth_scene(scene, index: int, path: Path, voice: str, rate: str,
                 use_cache: bool = True, table: dict[str, str] | None = None) -> list[dict]:
    """Voice ONE line, with retries, and return its raw word timings — carrying the
    text as WRITTEN, not as respelled for the voice. A cached result (same spoken
    text, voice and rate) is reused unless the caller forbids it; keying the cache on
    the spoken form means editing the pronounce table re-voices exactly the lines it
    touches, and nothing else."""
    table = table or {}
    spoken = _spoken(scene.text, table)
    raw_words: list[dict] = (_cached_words(path, spoken, voice, rate) or []) if use_cache else []
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS if not raw_words else 0):
        if attempt:
            base = _RETRY_DELAYS[attempt - 1]
            delay = base * (0.75 + 0.5 * random.random())
            log.info("TTS scene %d: attempt %d/%d — retry in %.1fs",
                     index, attempt + 1, _MAX_ATTEMPTS, delay)
            time.sleep(delay)
        try:
            # hard timeout: a throttled connection can hang far beyond edge-tts'
            # own socket timeouts and stall the whole batch
            raw_words = asyncio.run(
                asyncio.wait_for(_synth(spoken, voice, path, rate=rate), timeout=90)
            )
            # the picture shows the script's spelling, not the crutch fed to the voice
            raw_words = _as_written(raw_words, table)
            if not raw_words:
                log.warning("TTS scene %d attempt %d: connection OK but no word boundaries returned",
                            index, attempt + 1)
        except Exception as exc:
            last_exc = exc
            log.warning("TTS scene %d attempt %d/%d failed: %s: %s",
                        index, attempt + 1, _MAX_ATTEMPTS, type(exc).__name__, exc)
            raw_words = []
        if raw_words:
            _store_words(path, spoken, voice, rate, raw_words)
            break
    if not raw_words:
        detail = f" — last error: {last_exc}" if last_exc else " — server returned empty audio"
        raise RuntimeError(
            f"edge-tts returned no audio for scene {index} after {_MAX_ATTEMPTS} attempts{detail}"
        )
    return raw_words


def audio_path(job: VideoJob, index: int) -> Path:
    return job.workdir / "tts" / f"scene_{index:02d}.mp3"


def resynth_one(job: VideoJob, ctx: AppContext, index: int, rate: int | None = None) -> float:
    """Re-voice a single line right now, ignoring the cache, and write the result
    back into the scene. Used by the voiceover breakpoint, where the operator edits
    a line and wants to hear the new take without leaving the screen. Returns the
    fresh audio length.

    `rate` re-voices this ONE fragment at another speed and PINS it there
    (``scene.tts_rate``): the breakpoint's speed slider is a property of the take being
    made, not of the run, so a line the operator slowed down keeps its speed while the
    rest of the video keeps the run's. Pass None to voice it at whatever the line
    already uses.

    The sidecar cache is refreshed too, so the stage's own re-run on resume picks
    this take up instead of paying for the same synthesis twice."""
    scene = job.scenes[index]
    voice = _resolve_voice(ctx)
    if rate is not None:
        scene.tts_rate = int(rate)
    rate_pct = _scene_rate(scene, ctx)
    path = audio_path(job, index)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _synth_scene(scene, index, path, voice, rate_str(rate_pct),
                       use_cache=False, table=_pronounce(ctx))
    scene.audio = path
    src = duration_of(path)
    # timings stay scene-relative here; whichever stage lays the timeline out
    # (tts for info, footage for drama) converts them to absolute positions.
    scene.words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in raw]
    scene.audio_src_duration = src
    if not ctx.is_drama:
        scene.duration = src
    return src


def run(job: VideoJob, ctx: AppContext) -> None:
    voice = _resolve_voice(ctx)
    # drama: one clip per scene is the master timeline — record each line's length at
    # the speed it is spoken at + its scene-relative word timings; the footage stage
    # stretches (atempo) the voice to the generated clip and finalizes both
    # scene.duration and the absolute word positions.
    drama = ctx.is_drama
    table = _pronounce(ctx)
    audio_dir = job.workdir / "tts"
    audio_dir.mkdir(parents=True, exist_ok=True)

    offset = 0.0
    total = len(job.scenes)
    for i, scene in enumerate(job.scenes):
        path = audio_dir / f"scene_{i:02d}.mp3"
        raw_words = _synth_scene(scene, i, path, voice, rate_str(_scene_rate(scene, ctx)),
                                 table=table)
        scene.audio = path
        src = duration_of(path)
        if drama:
            # store natural length + scene-relative timings; footage sets the rest
            scene.audio_src_duration = src
            scene.words = [
                Word(text=w["text"], start=w["start"], end=w["end"]) for w in raw_words
            ]
        else:
            scene.duration = src
            scene.words = [
                Word(text=w["text"], start=offset + w["start"], end=offset + w["end"])
                for w in raw_words
            ]
            offset += scene.duration
        ctx.progress("tts", i + 1, total)
    # the target duration is a hint for the LLM, not a hard cap — accept whatever came out
