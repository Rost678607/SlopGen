"""Qwen3-TTS on this machine: cloning with no key, no upload and no per-character bill.

The price is time. Measured on a Ryzen 7 PRO 7840U with no discrete GPU: RTF 5.05,
so a minute of narration costs about five minutes of CPU. That is usable for a drama
rendered overnight and painful for anything interactive, which is why it is an engine
you choose rather than the default.

Both defaults below were measured, not assumed, and together they are worth 3.3x:

* **bfloat16** rather than float32. Zen 4 has AVX512-BF16, and the decode loop here is
  bound by memory bandwidth, so halving the weights nearly halves the time.
* **one thread per PHYSICAL core**, not per hardware thread. Going from 8 to 16 threads
  made it 2.4x SLOWER — the second thread on a core adds no bandwidth and only evicts
  the other one's cache. `threads = 0` means "count the physical cores", which is the
  right answer on every machine and the reason it is the default rather than a number.

The weights are not in the repository (2.3 GiB); the model manager fetches them and
this engine asks it for the folder, so a missing model fails with the command that
installs it rather than with a stack trace (see `models.store.ModelMissing`).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ..config.models import QwenLocalConfig
from .base import TTSError, Voice, apply_rate, trim_silence

log = logging.getLogger(__name__)

LANGUAGES = {
    "ru": "Russian", "en": "English", "zh": "Chinese", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "ja": "Japanese", "ko": "Korean",
}

# The codec runs at 12 Hz, so one second of speech is 12 tokens. The shipped
# generation config allows 8192 of them — eleven minutes of audio, which at this
# machine's RTF of 5 is an hour of CPU for one line.
CODEC_HZ = 12
# Measured on this project's own Russian lines: 13 characters per second of speech at
# the natural rate. Used only when the voice has no sample to measure — see
# `_chars_per_second`, which is the number that actually matters.
CHARS_PER_SECOND = 13.0
# A speaker slower or faster than this is a mismeasured sample, not a person.
MIN_CPS, MAX_CPS = 5.0, 25.0
LENGTH_HEADROOM = 2.5  # room for the model to FINISH a slow line before being cut off
MIN_TOKENS = 96  # a two-word line still needs room to finish its last syllable
# A take longer than this multiple of the estimate is not slow speech, it is the model
# having failed to stop. It has to sit BELOW `LENGTH_HEADROOM`, or the cap truncates
# every runaway to just under the threshold and the check can never fire — which is
# exactly what happened on the first attempt at this: a take measured at 2.4x the
# estimate sailed through a check set at 5x, because the cap had stopped it at 2.5x.
# Measured on three takes of one line: 0.97x, 1.37x, 2.39x — the third is the broken one.
RUNAWAY_FACTOR = 1.8


_RATE_CACHE: dict[str, float] = {}


def _chars_per_second(voice) -> float:
    """How fast THIS voice talks, taken from its own reference.

    A clone speaks at the pace of the sample it was cloned from, and the spread is
    large: the fixed 13 chars/s above was measured on a brisk catalogue voice, while a
    slow, halting sample here works out at 8.5 — and every line it says then comes out
    about 1.5x longer than the constant predicts. Judging such a voice by the constant
    condemns it as a runaway for speaking the way it was asked to. The reference
    carries both halves of the answer already: its transcript's length and its own
    duration."""
    if not voice.is_clone or not voice.ref_text.strip() or voice.ref_audio is None:
        return CHARS_PER_SECOND
    key = str(voice.ref_audio)
    if key not in _RATE_CACHE:
        from ..media.ffmpeg import duration_of

        rate = CHARS_PER_SECOND
        try:
            seconds = duration_of(voice.ref_audio)
            if seconds > 0.5:
                rate = min(MAX_CPS, max(MIN_CPS, len(voice.ref_text) / seconds))
        except Exception:  # noqa: BLE001 — an unreadable sample just keeps the default
            pass
        log.info("voice '%s' speaks at %.1f chars/s", voice.name, rate)
        _RATE_CACHE[key] = rate
    return _RATE_CACHE[key]


def _estimate_seconds(text: str, voice=None) -> float:
    """How long this line ought to take to say, at this voice's own pace. Deliberately
    generous — it is used to bound a runaway, not to predict a duration."""
    cps = _chars_per_second(voice) if voice is not None else CHARS_PER_SECOND
    return max(1.0, len(text) / cps)


def max_new_tokens(text: str, voice=None) -> int:
    """A ceiling on generation, derived from the text rather than left at the model's
    default.

    This is the single most important line in the module. Left uncapped, a base model
    that fails to emit its end-of-speech token simply keeps talking: measured here, one
    demo line ran for 44 minutes at 687% CPU and was still going, and an earlier take
    came back with words nobody had written appended to the end. Both are the same
    failure, caught at different points. The cap turns "forever" into "at most two and
    a half times longer than this line could possibly need"."""
    seconds = _estimate_seconds(text, voice) * LENGTH_HEADROOM
    return max(MIN_TOKENS, int(seconds * CODEC_HZ))


def physical_cores() -> int:
    """Cores, not hardware threads. `/proc/cpuinfo` pairs every logical CPU with the
    physical package and core it sits on, so counting the distinct pairs gets the
    number SMT hides; anything unparseable falls back to half the logical count."""
    try:
        pairs, cur = set(), {}
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if ":" not in line:
                if cur.get("physical id") is not None and cur.get("core id") is not None:
                    pairs.add((cur["physical id"], cur["core id"]))
                cur = {}
                continue
            k, _, v = line.partition(":")
            k = k.strip()
            if k in ("physical id", "core id"):
                cur[k] = v.strip()
        if cur.get("physical id") is not None and cur.get("core id") is not None:
            pairs.add((cur["physical id"], cur["core id"]))
        if pairs:
            return len(pairs)
    except OSError:
        pass
    return max(1, (os.cpu_count() or 2) // 2)


class QwenLocalEngine:
    id = "qwen-local"
    gives_timings = False
    clones = True
    native_rate = False
    suffix = ".wav"

    def __init__(self, cfg: QwenLocalConfig, models_root: Path | None):
        if models_root is None:
            raise TTSError("the local engine needs paths.models to be set")
        from ..models.store import ModelStore

        self.cfg = cfg
        self.model_dir = ModelStore(models_root).require(cfg.model_id)
        self._model = None  # loaded on first line: ~2.3 GiB and several seconds

    def expected_seconds(self, text: str, voice: Voice) -> float:
        """How long this line should take at this voice's own pace. Public because the
        caller judges the finished take, not this class."""
        return _estimate_seconds(text, voice)

    runaway_factor = RUNAWAY_FACTOR

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as e:
            raise TTSError(
                f"the local engine needs torch and qwen-tts ({e}) — run "
                "`slopgen models install qwen3-tts-0.6b`, which installs both"
            ) from None
        threads = self.cfg.threads or physical_cores()
        torch.set_num_threads(threads)
        dtype = getattr(torch, self.cfg.dtype, torch.bfloat16)
        log.info("loading %s on CPU (%s, %d threads)", self.cfg.model_id, self.cfg.dtype, threads)
        self._model = Qwen3TTSModel.from_pretrained(
            str(self.model_dir), device_map="cpu", dtype=dtype, attn_implementation="eager",
        )
        return self._model

    def synthesize(self, text: str, voice: Voice, rate: str, out_path: Path) -> None:
        if not voice.is_clone:
            raise TTSError(
                f"'{voice.name}' is a catalogue voice, and the local model has no "
                "catalogue — it only clones. Add a voice card under configs/voices/ "
                "(`slopgen voices add <sample.wav> --name … --text …`) and use its name."
            )
        if not voice.ref_text.strip():
            raise TTSError(
                f"voice '{voice.name}' has no `text`. The sample's transcript has to be "
                "typed by hand: a recognizer's mistakes do not stay put — the model "
                "reconciles a wrong transcript with the audio by drifting, and has been "
                "measured speaking words from the SAMPLE in the middle of a line."
            )
        import soundfile as sf

        model = self._load()
        cap = max_new_tokens(text, voice)
        log.info("qwen-local: %d chars -> at most %d tokens (%.0fs of audio)",
                 len(text), cap, cap / CODEC_HZ)
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=LANGUAGES.get(voice.lang, "English"),
            ref_audio=str(voice.ref_audio),
            ref_text=voice.ref_text,
            max_new_tokens=cap,
            temperature=self.cfg.temperature,
        )
        sf.write(str(out_path), wavs[0], sr)
        # judge the SPEECH, not the file: the padding is trimmed first, because a take
        # is a runaway when it says too much, not when it sits quiet afterwards
        trim_silence(out_path)
        # The length is NOT judged here. A take that runs long is usually a take with
        # somebody else's words glued to it, and only the caller has the recogniser
        # that can tell which words those are — judging first would reject a line that
        # is about to be repaired. See `base.verify_length`, called after the clip.
        apply_rate(out_path, rate)
        return None  # no word boundaries — the aligner takes it from here
