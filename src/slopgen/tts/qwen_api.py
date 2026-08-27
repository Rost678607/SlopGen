"""Qwen3-TTS through Alibaba's DashScope — the cheap cloud voice.

Half the price of Azure per character, with a free million for the first 90 days, and
it clones. What it does not do is tell you when each word was said, so every line
goes through the aligner afterwards — which is the trade the whole engine layer
exists to make explicit.

**Cloning here needs a URL, not a file.** The cloud model cannot be handed a local
sample: a voice is *enrolled* first, from a publicly reachable audio URL, and comes
back as an id used in place of a voice name. So a `configs/voices/` card that is to
work in the cloud needs `ref_url` filled in as well as `ref`; the same card with only
`ref` still clones locally (`qwen-local`), which uploads nothing anywhere. Enrolments
are cached next to the cards, because they persist for a year on Alibaba's side and
re-creating one on every run would burn the account's voice quota for nothing.

The international endpoint is the default: keys minted on `dashscope-intl` are
rejected by the mainland host and vice versa, and the failure reads as a plain 401.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

from ..config.models import QwenTTSConfig
from .base import TTSError, Voice, apply_rate, trim_silence

log = logging.getLogger(__name__)

# DashScope wants the language written out, not as a code
LANGUAGES = {
    "ru": "Russian", "en": "English", "zh": "Chinese", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ja": "Japanese", "ko": "Korean",
}

ENROLLED = ".enrolled.json"  # cache of {card+url+model: voice_id}, next to the cards
TIMEOUT = httpx.Timeout(connect=30.0, read=180.0, write=60.0, pool=30.0)


class QwenAPIEngine:
    id = "qwen"
    gives_timings = False
    clones = True
    native_rate = False  # no rate parameter — ffmpeg stretches the result
    suffix = ".wav"

    def __init__(self, cfg: QwenTTSConfig):
        key = os.environ.get(cfg.key_env, "")
        if not key:
            raise TTSError(
                f"{cfg.key_env} is not set (put it in .env), or pick another engine "
                "in configs/slopgen.toml [tts] / TUI Config → TTS"
            )
        self.cfg = cfg
        self.client = httpx.Client(
            base_url=cfg.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )

    # -- cloning ----------------------------------------------------------

    def _cache_path(self, voice: Voice) -> Path | None:
        return voice.ref_audio.parent / ENROLLED if voice.ref_audio else None

    def _cached_id(self, voice: Voice, url: str) -> str | None:
        path = self._cache_path(voice)
        if not path or not path.exists():
            return None
        try:
            return json.loads(path.read_text()).get(f"{voice.name}|{url}|{self.cfg.model}")
        except (OSError, ValueError):
            return None

    def _remember_id(self, voice: Voice, url: str, voice_id: str) -> None:
        path = self._cache_path(voice)
        if not path:
            return
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[f"{voice.name}|{url}|{self.cfg.model}"] = voice_id
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        except OSError:  # a lost cache only costs one enrolment
            pass

    def enroll(self, voice: Voice, url: str) -> str:
        cached = self._cached_id(voice, url)
        if cached:
            return cached
        r = self.client.post(
            "/services/aigc/tts/customization",
            json={"model": "voice-enrollment",
                  "input": {"action": "create_voice", "target_model": self.cfg.model,
                            "prefix": "slopgen", "url": url}},
        )
        if r.status_code >= 400:
            raise TTSError(f"voice enrolment failed ({r.status_code}): {r.text[:400]}")
        voice_id = (r.json().get("output") or {}).get("voice_id", "")
        if not voice_id:
            raise TTSError(f"voice enrolment returned no voice_id: {r.text[:400]}")
        log.info("enrolled '%s' with DashScope as %s", voice.name, voice_id)
        self._remember_id(voice, url, voice_id)
        return voice_id

    def _voice_id(self, voice: Voice) -> str:
        if not voice.is_clone:
            return voice.name
        url = getattr(voice, "ref_url", "") or ""
        if not url:
            raise TTSError(
                f"'{voice.name}' is a cloned voice, and the DashScope engine can only "
                "clone from a publicly reachable URL — set `ref_url` in "
                f"configs/voices/{voice.name}.toml, or use the 'qwen-local' engine, "
                "which clones from the local file and uploads nothing"
            )
        return self.enroll(voice, url)

    # -- synthesis --------------------------------------------------------

    def synthesize(self, text: str, voice: Voice, rate: str, out_path: Path) -> None:
        body = {
            "model": self.cfg.model,
            "input": {
                "text": text,
                "voice": self._voice_id(voice),
                "language_type": LANGUAGES.get(voice.lang, "English"),
            },
        }
        r = self.client.post("/services/aigc/multimodal-generation/generation", json=body)
        if r.status_code >= 400:
            raise TTSError(f"DashScope refused the line ({r.status_code}): {r.text[:400]}")
        audio = ((r.json().get("output") or {}).get("audio") or {})
        url = audio.get("url", "")
        if not url:
            raise TTSError(f"DashScope returned no audio: {r.text[:400]}")
        with httpx.stream("GET", url, timeout=TIMEOUT, follow_redirects=True) as resp:
            resp.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)
        trim_silence(out_path)
        apply_rate(out_path, rate)
        return None  # no word boundaries — the aligner takes it from here
