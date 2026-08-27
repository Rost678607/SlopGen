"""Edge TTS — the free default, and the reason the pipeline never needed an aligner.

edge-tts streams `WordBoundary` events alongside the audio, offsets in 100ns ticks,
which is where every subtitle timing in this project has always come from. The code
below is the same call the pipeline made before there were engines; keeping it
byte-for-byte identical is what makes `engine = "edge"` a true no-op upgrade.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from .base import Timing, Voice

# edge-tts >= 7 defaults to SentenceBoundary; we need per-word timings
BOUNDARY = "WordBoundary"
TIMEOUT_S = 90  # a throttled connection hangs far past edge-tts' own socket timeouts


async def _stream(text: str, voice: str, out_path: Path, rate: str) -> list[Timing]:
    import edge_tts

    words: list[Timing] = []
    com = edge_tts.Communicate(text, voice, rate=rate, boundary=BOUNDARY)
    with open(out_path, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == BOUNDARY:
                words.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 1e7,
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                })
    return words


class EdgeEngine:
    id = "edge"
    gives_timings = True
    clones = False
    native_rate = True
    suffix = ".mp3"

    def synthesize(self, text: str, voice: Voice, rate: str, out_path: Path) -> list[Timing]:
        return asyncio.run(
            asyncio.wait_for(_stream(text, voice.name, out_path, rate), timeout=TIMEOUT_S)
        )
