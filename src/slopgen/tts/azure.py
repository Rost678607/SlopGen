"""Azure Speech — edge-tts' paid sibling, and the only paid engine here that costs
the pipeline nothing architecturally.

It is the same synthesizer edge-tts talks to without a key, so it emits the same word
boundary events: no aligner, no recognizer download, no interpolation. What the key
buys is the catalogue — 700+ voices instead of fourteen — and the **Dragon HD Omni**
line, which is a different kind of model: it reads the surrounding sentences to decide
how to say the current one, so a line of dialogue comes out acted rather than
announced. Two knobs come with that and exist nowhere else:

* `temperature` — how much variation between takes. Low is a newsreader, high is a
  performance that will not repeat itself.
* `enhancePronunciation` — extra care with rare words and foreign names, which is
  where a Russian drama with invented proper nouns tends to embarrass a TTS.

Both are attributes of the SSML `voice` element and are silently ignored by the
classic neural voices, so the same SSML works either way.

Region matters more than it looks: the HD Omni voices are a preview hosted in a few
regions only, and asking for one outside them fails as "no such voice" rather than
"not available here" (see `config.models.AzureTTSConfig`).
"""

from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from ..config.models import AzureTTSConfig
from .base import Timing, TTSError, Voice

# regions that host the Dragon HD Omni preview
HD_REGIONS = ("eastus", "westeurope", "swedencentral", "southeastasia")

TICKS_PER_S = 1e7


def _lang_of(voice: str) -> str:
    """`ru-RU-SvetlanaNeural` -> `ru-RU`. SSML wants the locale on `<speak>`, and it
    has to match the voice or the service substitutes a default one."""
    parts = voice.split("-")
    return "-".join(parts[:2]) if len(parts) >= 3 else "en-US"


def build_ssml(text: str, voice: str, rate: str, cfg: AzureTTSConfig) -> str:
    attrs = f"name={quoteattr(voice)}"
    if "DragonHD" in voice:
        params = [f"temperature={cfg.temperature:g}"]
        attrs += f" parameters={quoteattr(' '.join(params))}"
        if cfg.enhance_pronunciation:
            attrs += ' enhancePronunciation="true"'
    body = escape(text)
    if rate and rate not in ("+0%", "0%"):
        body = f'<prosody rate="{escape(rate)}">{body}</prosody>'
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="http://www.w3.org/2001/mstts" '
        f'xml:lang="{_lang_of(voice)}"><voice {attrs}>{body}</voice></speak>'
    )


def _ticks(value) -> float:
    """Word-boundary durations arrive as a timedelta on some SDK versions and as raw
    100ns ticks on others."""
    if hasattr(value, "total_seconds"):
        return float(value.total_seconds())
    return float(value) / TICKS_PER_S


class AzureEngine:
    id = "azure"
    gives_timings = True
    clones = False
    native_rate = True
    suffix = ".mp3"

    def __init__(self, cfg: AzureTTSConfig):
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError:
            raise TTSError(
                "the Azure engine needs the Speech SDK — run "
                "`pip install azure-cognitiveservices-speech`"
            ) from None
        key = os.environ.get(cfg.key_env, "")
        if not key:
            raise TTSError(
                f"{cfg.key_env} is not set (put it in .env), or pick another engine "
                "in configs/slopgen.toml [tts] / TUI Config → TTS"
            )
        region = os.environ.get(cfg.region_env, "") or cfg.region
        self.sdk = speechsdk
        self.cfg = cfg
        self.region = region
        self._speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        self._speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3
        )

    def synthesize(self, text: str, voice: Voice, rate: str, out_path: Path) -> list[Timing]:
        if "DragonHD" in voice.name and self.region not in HD_REGIONS:
            raise TTSError(
                f"'{voice.name}' is a Dragon HD preview voice and is not served from "
                f"'{self.region}' — set [tts.azure].region to one of "
                f"{', '.join(HD_REGIONS)}"
            )
        audio_cfg = self.sdk.audio.AudioOutputConfig(filename=str(out_path))
        synth = self.sdk.SpeechSynthesizer(
            speech_config=self._speech_config, audio_config=audio_cfg
        )
        words: list[Timing] = []

        def _on_word(evt) -> None:
            btype = getattr(evt, "boundary_type", None)
            if btype is not None and btype != self.sdk.SpeechSynthesisBoundaryType.Word:
                return  # punctuation and sentence marks are not words
            start = float(evt.audio_offset) / TICKS_PER_S
            words.append({
                "text": evt.text,
                "start": start,
                "end": start + _ticks(getattr(evt, "duration", 0)),
            })

        synth.synthesis_word_boundary.connect(_on_word)
        result = synth.speak_ssml_async(build_ssml(text, voice.name, rate, self.cfg)).get()
        if result.reason != self.sdk.ResultReason.SynthesizingAudioCompleted:
            detail = self.sdk.CancellationDetails.from_result(result)
            raise TTSError(f"Azure refused the line: {detail.reason} — {detail.error_details}")
        return words
