"""Stage 3: synthesize each scene with edge-tts and capture word timings.

edge-tts streams WordBoundary events (offsets in 100ns ticks) alongside the
audio, which gives us subtitle timings for free — no Whisper needed.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

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


def run(job: VideoJob, ctx: AppContext) -> None:
    voice = _resolve_voice(ctx)
    # drama: one clip per scene is the master timeline — keep the voice at natural
    # speed here and record its length + scene-relative word timings; the footage
    # stage stretches (atempo) the voice to the generated clip and finalizes both
    # scene.duration and the absolute word positions.
    drama = ctx.is_drama
    audio_dir = job.workdir / "tts"
    audio_dir.mkdir(parents=True, exist_ok=True)

    offset = 0.0
    for i, scene in enumerate(job.scenes):
        path = audio_dir / f"scene_{i:02d}.mp3"
        raw_words: list[dict] = []
        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            if attempt:
                base = _RETRY_DELAYS[attempt - 1]
                delay = base * (0.75 + 0.5 * random.random())
                log.info("TTS scene %d: attempt %d/%d — retry in %.1fs",
                         i, attempt + 1, _MAX_ATTEMPTS, delay)
                time.sleep(delay)
            try:
                # hard timeout: a throttled connection can hang far beyond
                # edge-tts' own socket timeouts and stall the whole batch
                raw_words = asyncio.run(
                    asyncio.wait_for(_synth(scene.text, voice, path), timeout=90)
                )
                if not raw_words:
                    log.warning("TTS scene %d attempt %d: connection OK but no word boundaries returned",
                                i, attempt + 1)
            except Exception as exc:
                last_exc = exc
                log.warning("TTS scene %d attempt %d/%d failed: %s: %s",
                            i, attempt + 1, _MAX_ATTEMPTS, type(exc).__name__, exc)
                raw_words = []
            if raw_words:
                break
        if not raw_words:
            detail = f" — last error: {last_exc}" if last_exc else " — server returned empty audio"
            raise RuntimeError(
                f"edge-tts returned no audio for scene {i} after {_MAX_ATTEMPTS} attempts{detail}"
            )
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
    # the target duration is a hint for the LLM, not a hard cap — accept whatever came out
