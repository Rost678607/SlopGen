"""What every voice engine has to be, and the catalogue of the ones there are.

The pipeline asks one question of a synthesizer — "say this line, and tell me when
each word lands" — because word timings are what the subtitles, the pronounce table's
reverse mapping and the drama montage are all built on. Only edge-tts and Azure
answer the second half; everyone else returns `None`, which is this module's word for
"ask the aligner" (see :mod:`.align`). That is the entire reason for the abstraction:
NOT to hide differences between engines, but to make one specific difference — free
timings or recovered ones — the only thing the pipeline has to branch on.

Mirrors `llm/client.py`: :data:`ENGINES` is to voices what `PROVIDERS` is to models,
:data:`VOICE_PRESETS` is `MODEL_PRESETS`, and both are read by the TUI to build its
menus, so adding an engine adds it everywhere at once.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# a word timing, scene-relative: {"text": str, "start": float, "end": float}
Timing = dict


class TTSError(Exception):
    pass


@dataclass
class Voice:
    """Who speaks — one of two things, and every engine has to be told which.

    A CATALOGUE voice is a name the service knows (`ru-RU-SvetlanaNeural`). A CLONE
    is a sample of a real voice plus what is said in it; there is no profile stored
    anywhere, the pair is handed over with every single line. `configs/voices/*.toml`
    holds the second kind (see `config.models.VoiceConfig`), which is why the same
    card works on the local model and the cloud one without conversion."""

    name: str
    lang: str = "ru"
    ref_audio: Path | None = None
    ref_text: str = ""
    # cloud cloning enrols a sample from a URL rather than a file (see tts/qwen_api);
    # a card without one still clones locally, which uploads nothing
    ref_url: str = ""

    @property
    def is_clone(self) -> bool:
        return self.ref_audio is not None

    def __str__(self) -> str:
        return f"{self.name} (clone)" if self.is_clone else self.name

    @property
    def cache_key(self) -> str:
        """What the voiced-line cache is keyed on, which is NOT the same as the name.

        A catalogue voice is its name and nothing else. A clone is the pair, and the
        pair can change under a name that did not: re-record the sample, fix a typo in
        the transcript, and every cached line is now in a slightly different voice from
        the ones re-voiced after. So the sample's size and mtime and a digest of the
        transcript ride along, and editing either re-voices the video exactly as
        changing the voice would."""
        if not self.is_clone:
            return self.name
        try:
            st = self.ref_audio.stat()
            stamp = f"{st.st_size}:{int(st.st_mtime)}"
        except OSError:
            stamp = "missing"
        digest = hashlib.sha1(self.ref_text.encode("utf-8")).hexdigest()[:8]
        return f"clone:{self.name}|{stamp}|{digest}"


@runtime_checkable
class TTSEngine(Protocol):
    """One line in, one audio file out.

    `synthesize` returns the line's word timings **scene-relative**, or `None` when
    the engine has none to give — the caller then runs the aligner over the audio it
    just wrote. Returning an empty list is different and means failure: the engine
    claimed to have timings and produced none, which is how a throttled edge-tts
    connection fails, and the stage retries it."""

    id: str
    gives_timings: bool
    clones: bool
    native_rate: bool  # can vary speech rate itself; if not, ffmpeg does it after
    suffix: str  # container the engine writes (".mp3" / ".wav")

    def synthesize(self, text: str, voice: Voice, rate: str, out_path: Path) -> list[Timing] | None:
        ...


@dataclass(frozen=True)
class EngineInfo:
    """What the TUI and `slopgen models` need to know about an engine without
    importing it — importing `qwen-local` costs a torch import and 2.3 GiB of weights,
    so the menus are built from this instead."""

    id: str
    label: str
    description: str
    gives_timings: bool
    clones: bool
    catalogue: bool  # has named voices of its own, as opposed to cloning only
    key_envs: tuple[str, ...] = ()
    models: tuple[str, ...] = ()  # ids in slopgen.models.registry it cannot run without
    packages: tuple[str, ...] = ()
    tags: tuple[str, ...] = field(default_factory=tuple)


ENGINES: dict[str, EngineInfo] = {
    "edge": EngineInfo(
        id="edge",
        label="Edge TTS (free)",
        description=(
            "Microsoft's browser voices, no key, no cost, word timings included. "
            "Two Russian voices plus the twelve multilingual ones."
        ),
        gives_timings=True, clones=False, catalogue=True,
    ),
    "azure": EngineInfo(
        id="azure",
        label="Azure Speech",
        description=(
            "The paid catalogue behind edge-tts: 700+ voices including the Dragon HD "
            "Omni line, and the same word boundaries — the only alternative that needs "
            "no aligner. ~$22 per million characters, free tier 500k/month."
        ),
        gives_timings=True, clones=False, catalogue=True,
        key_envs=("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"),
        packages=("azure-cognitiveservices-speech>=1.40",),
    ),
    "qwen": EngineInfo(
        id="qwen",
        label="Qwen3-TTS (DashScope)",
        description=(
            "Alibaba's cloud voice: a catalogue plus zero-shot cloning from a sample "
            "you supply. ~$13 per million characters, and a free million for the "
            "first 90 days. No word timings — the aligner supplies them."
        ),
        gives_timings=False, clones=True, catalogue=True,
        key_envs=("DASHSCOPE_API_KEY",),
    ),
    "qwen-local": EngineInfo(
        id="qwen-local",
        label="Qwen3-TTS 0.6B (this machine)",
        description=(
            "The same cloning, offline and free, at the price of time: measured RTF "
            "5.05 on this CPU, so a minute of speech takes about five. Needs the "
            "2.3 GiB weights and torch from the model manager."
        ),
        gives_timings=False, clones=True, catalogue=False,
        models=("qwen3-tts-0.6b",),
        packages=("torch>=2.4", "qwen-tts", "soundfile>=0.12"),
    ),
}

# Voices worth offering in a menu. Not a complete catalogue for any engine — Azure
# alone has 700 — just the ones that are actually good for this pipeline's languages,
# so the operator picks from a short list and types a name only when they want to.
VOICE_PRESETS: dict[str, dict[str, list[str]]] = {
    "edge": {
        "ru": [
            "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural",
            "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural",
            "en-US-EmmaMultilingualNeural", "en-US-BrianMultilingualNeural",
            "de-DE-FlorianMultilingualNeural", "fr-FR-VivienneMultilingualNeural",
        ],
        "en": [
            "en-US-AriaNeural", "en-US-GuyNeural", "en-US-JennyNeural",
            "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural",
            "en-GB-SoniaNeural", "en-GB-RyanNeural",
        ],
    },
    "azure": {
        # Dragon HD Omni is a preview line and lives only in some regions — see
        # config.models.AzureTTSConfig.region
        "ru": [
            "ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural", "ru-RU-DariyaNeural",
            "ru-RU-Svetlana:DragonHDOmniLatestNeural",
            "ru-RU-Dmitry:DragonHDOmniLatestNeural",
        ],
        "en": [
            "en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural",
            "en-US-Ava:DragonHDLatestNeural", "en-US-Andrew:DragonHDLatestNeural",
            "en-US-Emma2:DragonHDLatestNeural", "en-US-Steffan:DragonHDLatestNeural",
        ],
    },
    "qwen": {
        "ru": ["Cherry", "Ethan", "Nofish", "Jada", "Dylan", "Sunny", "Peter"],
        "en": ["Cherry", "Ethan", "Nofish", "Jada", "Dylan", "Sunny", "Peter"],
    },
    "qwen-local": {"ru": [], "en": []},  # cloning only: the voice IS the sample
}


def voice_presets(engine: str, lang: str) -> list[str]:
    return VOICE_PRESETS.get(engine, {}).get(lang, [])


def gives_timings(engine: str) -> bool:
    info = ENGINES.get(engine)
    return bool(info and info.gives_timings)


def build(engine: str, cfg, lang: str = "ru", models_root: Path | None = None) -> TTSEngine:
    """Instantiate an engine by id. Imported lazily and one at a time: the Azure SDK
    and torch are both heavy, and neither should be a cost of running the free one."""
    if engine == "edge":
        from .edge import EdgeEngine

        return EdgeEngine()
    if engine == "azure":
        from .azure import AzureEngine

        return AzureEngine(cfg.azure)
    if engine == "qwen":
        from .qwen_api import QwenAPIEngine

        return QwenAPIEngine(cfg.qwen)
    if engine == "qwen-local":
        from .qwen_local import QwenLocalEngine

        return QwenLocalEngine(cfg.qwen_local, models_root)
    raise TTSError(f"unknown TTS engine '{engine}'. Known: {', '.join(ENGINES)}")


# Silence quieter than this, lasting longer than this, is padding rather than a pause.
SILENCE_DB = -45
SILENCE_PAD_S = 0.15  # kept at each end, so no consonant is clipped off the front


MAX_PAUSE_S = 0.6  # the longest gap that still reads as a pause rather than a stall


def trim_silence(path: Path) -> float:
    """Tidy the dead air in a take and return its new length.

    Two different jobs, done in one pass. The ENDS are cut off entirely: neural voices
    pad, and the info timeline lays scenes end to end by their audio length, so a line
    would sit in silence for as long as it spoke. Gaps INSIDE the line are capped at
    :data:`MAX_PAUSE_S` rather than removed, because the short ones are the delivery
    and taking them out turns narration into a list — while the long ones are the model
    stalling. Measured on three takes of one line: the speech ran 6.9s, 6.6s and 9.2s,
    but the files ran 9.0s, 12.7s and 22.2s, and in the worst take the extra thirteen
    seconds were not padding at the end but dead gaps in the middle of the sentence.

    Neural voices pad. Measured on three takes of one line from the local model: the
    speech lasted 6.9s, 6.6s and 9.2s, while the files ran 9.0s, 12.7s and 22.2s —
    the difference is dead air, in one case thirteen seconds of it. Left in, it is
    not merely untidy: the info timeline lays scenes end to end by their audio length,
    so a line would sit in silence for as long as it spoke, and the drama's fitting
    stage would stretch a clip to cover the emptiness.

    Only the ends are touched. The pauses INSIDE a line are the delivery, and removing
    those would turn narration into a list."""
    # stop_duration is how much of each silent run is KEPT, so this caps every internal
    # gap; the leading/trailing cut is the same filter run forwards and backwards.
    ends = (f"silenceremove=start_periods=1:start_silence={SILENCE_PAD_S}:"
            f"start_threshold={SILENCE_DB}dB:detection=peak")
    gaps = (f"silenceremove=stop_periods=-1:stop_duration={MAX_PAUSE_S}:"
            f"stop_threshold={SILENCE_DB}dB:detection=peak")
    trim = f"{ends},areverse,{ends},areverse,{gaps}"
    from ..media.ffmpeg import duration_of

    tmp = path.with_name(path.name + ".trim" + path.suffix)
    try:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af", trim, str(tmp)],
                       check=True, capture_output=True)
        if duration_of(tmp) > 0.05:  # never replace a take with nothing
            tmp.replace(path)
        else:
            tmp.unlink(missing_ok=True)
    except (subprocess.CalledProcessError, OSError):
        tmp.unlink(missing_ok=True)  # a trim that fails costs padding, not the take
    return duration_of(path)


# Below this much of the line actually being in the take, it is not this line (see
# `align._script_span` for what the number measures — letters of the script found in
# the span it was found in, not words matched). Measured over 24 takes from two
# references: takes that say the line score 0.53-0.94, takes that recite the sample
# score 0.06-0.44, and nothing lands in between.
MIN_SCRIPT_MATCH = 0.5


def verify_take(engine, text: str, voice: Voice, seconds: float,
                matched: float = 1.0) -> None:
    """Reject a take that still says far too much AFTER the surplus was cut away.

    Called by whoever owns the recogniser, once the clip has removed the words that
    do not belong to the line — because before that a perfectly good line arrives
    bracketed by leaked reference text and looks like a runaway. Engines that cannot
    run away (a catalogue voice reading a line) expose no estimate and are skipped."""
    expected = getattr(engine, "expected_seconds", None)
    if expected is None:
        return
    if matched < MIN_SCRIPT_MATCH:
        raise TTSError(
            f"the take does not say this line: only {matched:.0%} of it is in there. "
            "The model recited the reference instead of the text it was given. The "
            "next attempt rolls again; if it keeps happening the reference sample is "
            "the thing to change, and `slopgen voices check <name>` will say what is "
            "wrong with it — what a reference has to be is ONE continuous take of one "
            "person, transcribed by hand from that very recording. Its length is not "
            "the problem: 6s and 61s were measured cloning equally well."
        )
    est = expected(text, voice)
    if seconds > est * getattr(engine, "runaway_factor", 1.8):
        raise TTSError(
            f"the take is not this line: {seconds:.1f}s of speech where about "
            f"{est:.1f}s was expected at this voice's pace, and cutting what the "
            "recognizer could not match to the script did not bring it back. "
            "Generation is sampled, so the next attempt rolls again; if it keeps "
            "happening, the reference sample is the thing to change — ONE continuous "
            "take of one person, transcribed by hand from that very recording."
        )


def apply_rate(path: Path, rate: str) -> None:
    """Speed a finished file up or down, for the engines that have no rate parameter.

    Deliberately the same `atempo` the footage stage already stretches voice with, so
    a line voiced at +20% by Qwen sounds like the same operation as a line voiced at
    +20% by edge-tts — one of them just pays for it afterwards."""
    factor = rate_factor(rate)
    if abs(factor - 1.0) < 0.005:
        return
    from ..media.ffmpeg import stretch_audio

    tmp = path.with_name(path.name + ".rate" + path.suffix)
    stretch_audio(path, tmp, factor)
    tmp.replace(path)


def rate_factor(rate: str) -> float:
    """`"+20%"` as an atempo multiplier, for the engines that cannot vary speed
    themselves and have to be stretched afterwards."""
    try:
        return max(0.5, min(2.0, 1.0 + int(str(rate).strip().rstrip("%")) / 100.0))
    except ValueError:
        return 1.0
