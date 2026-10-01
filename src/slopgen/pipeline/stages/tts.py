"""Stage 3: give every scene a voice and word timings.

**The stage no longer knows how to speak.** It picks an engine out of
:mod:`slopgen.tts`, hands it a line and gets audio back; whether the timings come
free with that audio or have to be recovered afterwards is the one difference it
branches on. edge-tts and Azure stream `WordBoundary` events, so the timings are
exact and cost nothing. Everyone else — the Qwen models, and a human at a microphone —
returns audio and nothing else, and :mod:`slopgen.tts.align` reads the timings back
off the audio against the script we already have.

Each line's raw (scene-relative) timings are cached next to its audio as
``scene_NN.json``, keyed by the exact text, voice, rate AND engine that produced them.
A re-run — after a crash, or after the operator edited the narration at the TTS
breakpoint — then re-synthesizes only the lines whose text actually changed and reuses
the rest. The engine is part of that key for the same reason the rate is: switching
voices must re-voice everything, not leave half a video in the old one.

**Speed is per line, not per run.** The run's ``tts_rate`` is what every line is
voiced at by default, in both modes; a line the operator re-voiced at the breakpoint's
speed slider carries its own ``Scene.tts_rate`` and keeps it. Since the cache is keyed
on the rate as well, changing a line's speed re-synthesizes that line and nothing else.
An engine with no speed parameter of its own (`native_rate = False`) is stretched with
`atempo` afterwards, which is the same operation the footage stage already performs on
a drama's voice.

**The delivery is per line too, and for the same reason the speed is.** No cloning
engine here has a parameter for intonation; what it has is the sample, whose reading
it imitates. So a card holds every recording of one person on one level
(`config.models.VoiceConfig.samples`) and names one of them its default; a line can
name another — ``Scene.voice = "марта:зло"``, resolved at synthesis time and part of
the cache key, so re-pinning one line re-voices that line and no other. A line that
names nothing is voiced by the run's voice, which is the whole video's delivery, and
which follows the card's default rather than being a copy of it: move the default in
the editor and every unpinned line is re-voiced with the new one.

**Who chooses the delivery can be the writer.** With `tts_deliveries` on, one pass
over the finished script (`llm/delivery.py`) pins the lines that are shouted or
whispered to the recording that is, before anything is voiced — the card's deliveries
are the only vocabulary it may use, the share of lines it may recast is capped, and
everything it leaves alone keeps the empty spec and therefore the card's default. A pin
the operator made by hand is never overwritten by it (`Scene.voice_auto`), and an
automatic pin on a line whose text has since changed is taken back.

**What is spoken is not always what is written.** A few words come out wrong no
matter how they are spelled in the script — a Cyrillic acronym whose letters form
a pronounceable syllable gets read as that syllable, so «НЛО» is said "нло"
instead of spelled out. The run's `pronounce` table (config `[tts.pronounce.<lang>]`)
respells those for the synthesizer only; the subtitles keep the original, because
the picture should read «НЛО». That mirrors what `--clean-subs` does in the other
direction, where the voice keeps every word and only the burned-in text changes.
The respelling is one token by construction (see `TTSConfig`), so the word timings
line up with the original and nothing has to be re-spread — the display text is
simply swapped back on the way out. This is engine-independent by construction: the
table fixes what a synthesizer does with a spelling, and every engine, including the
aligner's recognizer, is fed the same respelled text.

**The voice need not be synthesized at all.** With ``--tts-source manual`` the stage
writes the script out and waits for wav files, exactly as the footage stages wait for
clips (:mod:`..manual_tts`) — for the good voices that have no API, and for an
operator who would rather read the lines themselves.
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

from ...media.ffmpeg import duration_of, stretch_audio
from ...tts import (
    ENGINES,
    TTSError,
    Voice,
    build as build_engine,
    rate_factor,
    varies_rate,
    verify_take,
)
from ...tts import align as aligner
from ..context import AppContext
from ..job import VideoJob, Word

# Delays between retry attempts (seconds). Jitter of ±25% is applied at runtime
# to avoid thundering-herd when multiple scenes retry simultaneously in a batch.
_RETRY_DELAYS = (3.0, 8.0, 20.0, 35.0)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1

# fallback narrator voices when there is no content-type voice to borrow (drama
# mode) and the run didn't set an explicit voice_override. Per engine, because a
# voice name means nothing outside the catalogue it belongs to; an engine with no
# catalogue at all (local cloning) has no default and says so.
_DEFAULT_VOICES: dict[str, dict[str, str]] = {
    "edge": {"ru": "ru-RU-SvetlanaNeural", "en": "en-US-AriaNeural"},
    "azure": {"ru": "ru-RU-SvetlanaNeural", "en": "en-US-AvaMultilingualNeural"},
    "qwen": {"ru": "Cherry", "en": "Cherry"},
    "qwen-local": {},
}


def resolve_engine(ctx: AppContext) -> str:
    return ctx.params.tts_engine or ctx.g.tts.engine or "edge"


def run_voice_spec(store, params, engine: str = "") -> str:
    """The voice spec a line that names none is voiced with: the run's override, else
    the content type's voice for this language, else the engine's own default.

    Takes the store and the params rather than a context, because the montage screen
    asks it too — to show which voice a line is currently speaking with — and building
    a context there would open an LLM client for a question that has nothing to do with
    one (see `web/montage_api.context`)."""
    if params.voice_override:
        return params.voice_override
    engine = engine or params.tts_engine or store.global_cfg.tts.engine or "edge"
    ct = store.content_types.get(params.content_type) if params.content_type else None
    if ct is not None:
        v = ct.voices.get(params.lang)
        if v:
            return v
    return _DEFAULT_VOICES.get(engine, {}).get(params.lang, "")


def _voice_name(ctx: AppContext, engine: str) -> str:
    return run_voice_spec(ctx.store, ctx.params, engine)


def _resolve_voice(ctx: AppContext, engine: str, spec: str = "") -> Voice:
    """Turn a name into either a catalogue voice or a cloning pair.

    One namespace on purpose: `--voice марта` and `--voice ru-RU-SvetlanaNeural` are
    the same option, and which kind it is depends only on whether a card of that name
    exists under `configs/voices/`. That keeps the cloned voices usable everywhere a
    voice name is accepted today, without a second flag to remember.

    `марта:зло` is the same namespace one level down: one of the card's deliveries,
    which is how an intonation is chosen (see `config.models.VoiceConfig`). A bare
    `марта` is the delivery that card calls its default, and the difference between
    the two is worth stating: the first is pinned to a recording, the second follows
    the card and moves when the editor moves it.

    What comes back is named after the RESOLVED recording (`марта:зло`) whichever way
    it was asked for, because that name is what the voiced-line cache is keyed on. So
    two specs meaning the same recording share their cached takes, and moving a card's
    default re-voices every line that was following it — which is the whole point of
    the flag.

    `spec` overrides the run's voice, and is how ONE line comes out in another
    delivery: it is the spec `Scene.voice` holds. Empty means the run's own."""
    lang = ctx.params.lang
    name = spec or _voice_name(ctx, engine)
    found = ctx.store.voice_sample(name) if name else None
    if found is not None:
        card, sample, which = found
        ref = sample.ref_path
        if ref is None or not Path(ref).exists():
            raise RuntimeError(
                f"voice '{name}' points at a recording that is not there "
                f"('{which}' → {sample.ref or '<no ref>'}) — fix "
                f"configs/voices/{card.name}.toml"
            )
        return Voice(name=f"{card.name}:{which}", lang=card.lang or lang,
                     ref_audio=Path(ref), ref_text=sample.text,
                     ref_url=sample.ref_url)
    info = ENGINES.get(engine)
    if not name or (info is not None and not info.catalogue):
        # Reached with a NAME only when the engine cannot read one: everything it could
        # have spoken is a card, and this is not a card. Saying so here is the whole
        # point — the alternative is a cloning model being handed `ru-RU-Svetlana…`
        # and failing an hour later with whatever a local model says about a sample
        # it never got.
        known = ", ".join(ctx.store.voice_specs()) or "none yet"
        raise RuntimeError(
            f"engine '{engine}' has no voice catalogue — it only clones. Pick one of "
            f"your voice cards ({known}) with --voice, or create one: "
            "`slopgen voices add <sample.wav> --name … --text …`"
        )
    return Voice(name=name, lang=lang)


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


def _cached_words(audio: Path, text: str, voice: str, rate: str, engine: str) -> list[dict] | None:
    """Timings from a previous run, but only if the audio next to them was made
    from exactly this text by the same engine with the same voice and rate."""
    cache = _cache_path(audio)
    if not (audio.exists() and cache.exists()):
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (data.get("text") != text or data.get("voice") != voice
            or data.get("rate") != rate or data.get("engine", "edge") != engine):
        return None
    words = data.get("words")
    return words if isinstance(words, list) and words else None


def _store_words(audio: Path, text: str, voice: str, rate: str, engine: str,
                 words: list[dict]) -> None:
    try:
        _cache_path(audio).write_text(
            json.dumps({"text": text, "voice": voice, "rate": rate, "engine": engine,
                        "words": words}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:  # a missing cache only costs a re-synthesis
        pass


# The rate a RAW take is recorded at in its sidecar: none at all. It is the model's own
# pace by definition, and keying it on the rate that was asked for would make the one
# file that is independent of the question look like an answer to it.
RAW_RATE = "raw"


def raw_path(take: Path) -> Path:
    """Where a line's UNSTRETCHED take lives: `scene_03.raw.wav` beside `scene_03.wav`.

    Only engines that cannot vary their own speed have one (`native_rate`), and it is
    what makes a change of pace cost an `atempo` instead of a generation. A second file
    per line and not a factor written on the scene, because `atempo` of an `atempo`
    compounds: every pace has to be derived from the model's own reading, or the tenth
    adjustment is audibly worse than the first for no reason the operator can see."""
    return take.with_name(take.stem + ".raw" + take.suffix)


def lay_rate(raw: Path, out: Path, factor: float) -> None:
    """Put the raw take out at `factor` of its own speed. A factor of one is a copy,
    because the invariant is worth more than the file: `out` is always derived from
    `raw`, so there is one rule and no branch anywhere asking which file to read."""
    if abs(factor - 1.0) < 0.005:
        shutil.copy2(raw, out)
        return
    stretch_audio(raw, out, factor)


def rescale(words: list[dict], factor: float) -> list[dict]:
    """The same words, timed for a take played at `factor` of the speed it was said at.

    `atempo` is a linear map of time and nothing else — no pitch shift, no resampling of
    events — so every timing simply divides. This is why a change of pace needs no second
    recogniser pass and why it cannot drift out of step with the audio."""
    if abs(factor - 1.0) < 0.005:
        return words
    return [{**w, "start": w["start"] / factor, "end": w["end"] / factor} for w in words]


def _pronounce(ctx: AppContext) -> dict[str, str]:
    """The run language's respelling table, or an empty one."""
    return ctx.g.tts.pronounce.get(ctx.params.lang, {})


def rate_str(percent: int) -> str:
    """A speech rate as the engines want it: ``"+20%"``, ``"-15%"``, ``"+0%"``."""
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


# What the probe asks the voice to say. Short on purpose: a cloner that is going to
# recite its reference does it worst on a short line, because it finishes what it was
# asked for and keeps going — measured on the sample that prompted all of this, a
# 54-character line survived two takes in three and a 32-character one none in three.
# A probe that a broken voice could pass would be worse than no probe.
PROBE_TEXT = {
    "ru": "Ключи он оставил на подоконнике.",
    "en": "He left the keys on the windowsill.",
}
PROBE_ATTEMPTS = 2


def _probe_voice(engine, voice: Voice, model_dir: Path) -> str:
    """Voice one short line and find out whether the model says it. Empty when it
    does; otherwise why it did not, in the words of whoever rejected it.

    Generation is sampled, so one bad take is a dice roll rather than a verdict —
    hence two."""
    import tempfile

    text = PROBE_TEXT.get(voice.lang, PROBE_TEXT["en"])
    last = ""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / f"probe{engine.suffix}"
        for _attempt in range(PROBE_ATTEMPTS):
            try:
                engine.synthesize(text, voice, rate_str(0), out)
                _w, seconds, matched = aligner.clip_to_script(
                    out, text, model_dir, duration_of(out))
                verify_take(engine, text, voice, seconds, matched)
                return ""
            except TTSError as e:
                last = str(e)
            except Exception as e:  # noqa: BLE001 — a broken engine is not a broken voice
                log.warning("probe could not run (%s: %s)", type(e).__name__, e)
                return ""
    return last or "the engine returned nothing"


def _check_reference(voice: Voice, engine, model_dir: Path, fatal: bool) -> None:
    """Listen to the cloning sample before the run voices anything with it.

    A zero-shot cloner is handed the sample and its transcript with every line, and
    what it does with a pair that does not agree is not to sound worse — it finishes
    the transcript out loud, in the middle of the script. Measured on the card that
    sent this stage looking for a check: 5 of its 51 transcript words are findable in
    the audio, 60% of its 33 seconds is silence, and three takes out of three of a
    five-word line came back saying the SAMPLE.

    A check rather than a repair, because the repair was tried first. Cutting the
    sample down to a window — at a word boundary, transcript cut to match — moved the
    median share of the script actually present in a take from 0.10 (33s) to 0.00
    (13.5s) to 0.58 (10s): shorter is better, the trend is real, and none of it is a
    voice anybody would ship. There is nothing in a bad sample to recover, so the only
    honest thing to do with one is say so early.

    It costs one pass of the recogniser over one file, and it buys the whole
    difference between "this sample cannot work, here is what is wrong with it" and
    three slow takes, a generic complaint, and a dead run — which on a local model is
    twenty minutes of CPU before the first word of the news."""
    from ...tts import refs

    report = refs.check_transcript(voice.ref_audio, voice.ref_text, model_dir)
    log.info("voice '%s': %s", voice.name, report.summary())
    for level, message in report.problems:
        log.warning("voice '%s': %s", voice.name, message)
    if report.usable or not fatal:
        return

    # The report is a suspicion, and a suspicion is not grounds to refuse somebody
    # else's recording. Settle it the only way that cannot be argued with: ask the
    # model to say one short line and see whether it says it. One synthesis — half a
    # minute on the local engine — against the twenty it costs to find out at the end
    # of the stage, and it accuses nothing that has not actually happened.
    log.warning("voice '%s': the sample scores badly, voicing one line to find out "
                "whether it matters", voice.name)
    failure = _probe_voice(engine, voice, model_dir)
    if not failure:
        log.warning("voice '%s': scores badly and works anyway — going on. If lines "
                    "come back with somebody else's words in them, this is why",
                    voice.name)
        return
    faults = " ".join(m for level, m in report.problems if level == "error")
    raise TTSError(
        f"voice '{voice.name}' cannot say a line. Asked for "
        f"«{PROBE_TEXT.get(voice.lang, PROBE_TEXT['en'])}» {PROBE_ATTEMPTS} times "
        f"before the run started, and every take was rejected — {failure} "
        f"The sample is what to change: {faults}. Measured: {report.summary()}. "
        "A reference has to be ONE continuous take of one person with the transcript "
        "typed out by hand from that very recording; its LENGTH is not the gate — "
        "clean references of 6s and of 61s were measured cloning equally well, a long "
        "one only costs more CPU per line. `slopgen voices check <name>` scores a "
        "sample before a run commits to it. Set [tts] check_reference = false to "
        "synthesize anyway."
    )


class _Speaker:
    """One engine, ready to voice this run's lines, with the aligner it needs.

    Built once per stage entry rather than per line: an engine may hold a network
    session, and the local one holds 2.3 GiB of weights. Everything that can be known
    to be missing — an unset key, an uninstalled recognizer — is discovered here, in
    the constructor, before the first line is spoken, so a run that cannot finish
    fails in seconds instead of after an hour of synthesis."""

    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.id = resolve_engine(ctx)
        self.engine = build_engine(self.id, ctx.g.tts, ctx.params.lang, ctx.g.paths.models)
        self.voice = _resolve_voice(ctx, self.id)
        self.suffix = self.engine.suffix
        # A network line is worth retrying five times. The local model is different on
        # both counts: its failures are not transient, they are DICE — generation is
        # sampled, so the same line comes back at 0.97x, 1.37x and 2.39x its expected
        # length on three runs, and the third is rejected as a runaway. Re-rolling
        # therefore helps, which it does not for a wrong API key. Three, not five,
        # because each roll costs real minutes of CPU rather than a round trip.
        self.attempts = 3 if self.id == "qwen-local" else _MAX_ATTEMPTS
        # backing off helps a throttled server and does nothing for a local dice roll
        self.retry_delay = self.id != "qwen-local"
        self.align_dir = None if self.engine.gives_timings else _require_aligner(ctx)
        # Resolved voices, by the spec that asked for them; the run's own sits under
        # the empty spec, which is what a line that names nothing asks for. `_checked`
        # is keyed by the SAMPLE rather than by the spec, so the same recording reached
        # two ways — as the run's voice and as `марта` spelled out on a line — is
        # listened to once.
        self._voices: dict[str, Voice] = {"": self.voice}
        self._checked: set[str] = set()
        self._listen(self.voice)

    def voice_for(self, spec: str) -> Voice:
        """Who says THIS line: the run's voice, or the one the line names.

        A line carries a spec and not a resolved voice (`Scene.voice`), because a spec
        is the operator's choice and survives everything the card does afterwards — a
        re-recorded sample or a fixed transcript reaches the lines pinned to it the
        next time a speaker is built, exactly as it reaches the run's own voice.

        Resolved once per spec and kept for as long as this speaker lives, because
        resolution is cheap but what follows it is not: a cloning sample is listened to
        before it is trusted, and doing that per line would be a recognizer pass per
        line."""
        if spec not in self._voices:
            self._voices[spec] = _resolve_voice(self.ctx, self.id, spec)
            self._listen(self._voices[spec])
        return self._voices[spec]

    def _listen(self, voice: Voice) -> None:
        """Check a cloning sample before anything is voiced with it, once per sample."""
        if not (self.engine.clones and voice.is_clone and self.align_dir is not None):
            return
        if voice.cache_key in self._checked:
            return
        self._checked.add(voice.cache_key)
        _check_reference(voice, self.engine, self.align_dir, self.ctx.g.tts.check_reference)

    def speak(self, spoken: str, path: Path, rate: str, voice: Voice | None = None) -> list[dict]:
        """Voice one line into `path` at `rate`, and return its word timings.

        Two shapes, decided by `native_rate`. An engine that has a rate parameter is
        handed the rate and writes the finished take straight out. One that has not
        speaks at its own pace into the RAW take beside it (:func:`raw_path`), and the
        pace is applied here, afterwards, by stretching that file into `path`.

        Both halves of the second shape matter. The raw take survives, so a later change
        of speed is one `atempo` away and is the same reading at another pace rather than
        a fresh roll of a sampled model (see :func:`restretch_one`). And the words are
        RESCALED rather than recovered: `atempo` is a linear map of time, so a word at
        `t` in the raw take is at `t / factor` in the stretched one — exact arithmetic,
        where asking the recogniser again would be a second pass for an answer already
        held.
        """
        voice = voice or self.voice
        stretch = not self.engine.native_rate
        factor = rate_factor(rate) if stretch else 1.0
        # the model writes where it speaks: into the take itself when the rate was its
        # to apply, and into the raw one when the pace is still to be put on
        heard = raw_path(path) if stretch else path
        # The sidecar is the CLAIM that the file beside it is this line, said this way.
        # It is withdrawn before the file is overwritten and only made again once the
        # take has been accepted — because a take this engine rolls can be rejected
        # (`verify_take`), and a rejected roll left under a claim its predecessor earned
        # is a file `restretch_one` would hand back later as the operator's good take.
        _cache_path(heard).unlink(missing_ok=True)
        words = self.engine.synthesize(spoken, voice, rate, heard)
        if words is None:
            # A cloning model is shown the reference transcript as an example and does
            # not always stop at the line it was asked for; the recogniser can tell
            # which heard words belong to the script, so the rest is cut away rather
            # than shipped as narration nobody wrote.
            #
            # Judged on the RAW take, which is the honest place for it: the length an
            # expected-seconds estimate is about is the voice's own pace, not a pace
            # somebody asked for afterwards.
            if self.engine.clones:
                words, seconds, matched = aligner.clip_to_script(
                    heard, spoken, self.align_dir, duration_of(heard))
                verify_take(self.engine, spoken, voice, seconds, matched)
            else:
                words = aligner.align(heard, spoken, self.align_dir, duration_of(heard))
        if not stretch:
            return words
        # the raw take's own timings, cached beside it: they are what a later re-stretch
        # rescales, and they are what this one rescales now
        _store_words(heard, spoken, voice.cache_key, RAW_RATE, self.id, words)
        lay_rate(heard, path, factor)
        return rescale(words, factor)


# ONE live speaker, kept between calls. The breakpoint's 🔊 re-voice button calls
# `resynth_one` once per take, and the local engine holds 2.3 GiB of weights that take
# half a minute to load — reloading them between two takes of the same line would make
# the one screen built for listening unusable. A single slot rather than a dictionary
# for the same reason: two of those weight sets in memory at once is not a cache, it is
# a leak. The key covers everything that changes what a speaker IS, including the
# engine's own settings, so editing the config in the TUI rebuilds rather than serves
# the old one.
_SPEAKER: tuple[tuple, "_Speaker"] | None = None


def _speaker_for(ctx: AppContext) -> "_Speaker":
    global _SPEAKER

    key = (resolve_engine(ctx), ctx.params.voice_override, ctx.params.lang,
           ctx.params.content_type, ctx.params.tts_source, ctx.g.tts.model_dump_json())
    if _SPEAKER is None or _SPEAKER[0] != key:
        _SPEAKER = (key, _Speaker(ctx))
    return _SPEAKER[1]


def _require_aligner(ctx: AppContext) -> Path:
    """The recognizer folder for this run's language, or an error naming the command
    that installs it. Called before any synthesis for exactly that reason."""
    from ...models.store import ModelStore

    model_id = aligner.model_for(ctx.params.lang, ctx.g.tts)
    return ModelStore(ctx.g.paths.models).require(model_id)


def _synth_scene(scene, index: int, path: Path, speaker: "_Speaker", rate: str,
                 use_cache: bool = True, table: dict[str, str] | None = None) -> list[dict]:
    """Voice ONE line, with retries, and return its raw word timings — carrying the
    text as WRITTEN, not as respelled for the voice. A cached result (same spoken
    text, engine, voice and rate) is reused unless the caller forbids it; keying the
    cache on the spoken form means editing the pronounce table re-voices exactly the
    lines it touches, and nothing else.

    WHO says it is the line's own business too (`Scene.voice`): a line pinned to
    another recording of the same person is voiced with that one, and since the cache
    key carries the recording, re-pinning a line re-voices that line alone."""
    table = table or {}
    spoken = _spoken(scene.text, table)
    voice = speaker.voice_for(scene.voice)
    voice_key = voice.cache_key
    raw_words: list[dict] = (
        (_cached_words(path, spoken, voice_key, rate, speaker.id) or []) if use_cache else []
    )
    last_exc: Exception | None = None
    for attempt in range(speaker.attempts if not raw_words else 0):
        if attempt and speaker.retry_delay:
            base = _RETRY_DELAYS[attempt - 1]
            delay = base * (0.75 + 0.5 * random.random())
            log.info("TTS scene %d: attempt %d/%d — retry in %.1fs",
                     index, attempt + 1, speaker.attempts, delay)
            time.sleep(delay)
        try:
            raw_words = speaker.speak(spoken, path, rate, voice)
            # the picture shows the script's spelling, not the crutch fed to the voice
            raw_words = _as_written(raw_words, table)
            if not raw_words:
                log.warning("TTS scene %d attempt %d: audio arrived but no word timings",
                            index, attempt + 1)
        except Exception as exc:
            last_exc = exc
            log.warning("TTS scene %d attempt %d/%d failed: %s: %s",
                        index, attempt + 1, speaker.attempts, type(exc).__name__, exc)
            raw_words = []
        if raw_words:
            _store_words(path, spoken, voice_key, rate, speaker.id, raw_words)
            break
    if not raw_words:
        detail = f" — last error: {last_exc}" if last_exc else " — the engine returned nothing"
        raise RuntimeError(
            f"{speaker.id} returned no audio for scene {index} after "
            f"{speaker.attempts} attempt(s){detail}"
        )
    return raw_words


def audio_path(job: VideoJob, index: int, suffix: str = ".mp3") -> Path:
    return job.workdir / "tts" / f"scene_{index:02d}{suffix}"


def resynth_one(job: VideoJob, ctx: AppContext, index: int, rate: int | None = None,
                voice: str | None = None) -> float:
    """Re-voice a single line right now, ignoring the cache, and write the result
    back into the scene. Used by the voiceover breakpoint, where the operator edits
    a line and wants to hear the new take without leaving the screen. Returns the
    fresh audio length.

    `rate` re-voices this ONE fragment at another speed and PINS it there
    (``scene.tts_rate``): the breakpoint's speed slider is a property of the take being
    made, not of the run, so a line the operator slowed down keeps its speed while the
    rest of the video keeps the run's. Pass None to voice it at whatever the line
    already uses.

    `voice` is the same arrangement for WHO says it (``scene.voice``) — which on a
    cloning engine is how it is said, since another recording of the same person is
    the only intonation control there is. `""` puts the line back on the run's voice;
    None leaves whatever it already uses alone.

    The sidecar cache is refreshed too, so the stage's own re-run on resume picks
    this take up instead of paying for the same synthesis twice."""
    scene = job.scenes[index]
    if scene.hush:
        raise ValueError("this is a pause — there is nothing in it to say")
    speaker = _speaker_for(ctx)
    if rate is not None:
        scene.tts_rate = int(rate)
    if voice is not None:
        scene.voice = str(voice)
        # by hand, so the casting pass may not take it back on the next entry to the
        # stage — including the empty spec, which is the operator saying "back to the
        # card's own delivery" and not the absence of a decision
        scene.voice_auto = False
    rate_pct = _scene_rate(scene, ctx)
    path = audio_path(job, index, speaker.suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _synth_scene(scene, index, path, speaker, rate_str(rate_pct),
                       use_cache=False, table=_pronounce(ctx))
    scene.audio = path
    src = duration_of(path)
    # timings stay scene-relative here; whichever stage lays the timeline out
    # (tts for info, footage for drama) converts them to absolute positions.
    scene.words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in raw]
    scene.audio_src_duration = src
    if not ctx.is_beats:
        scene.duration = src
    return src


def restretch_one(job: VideoJob, ctx: AppContext, index: int, rate: int) -> float | None:
    """Give one line another pace WITHOUT voicing it again. Returns the new length, or
    None when this take cannot be re-stretched and has to be spoken afresh.

    This exists because on an engine that cannot vary its own speed, the pace was never
    part of the performance: the model read the line once, and what the rate did was
    stretch the result (see :meth:`_Speaker.speak`). Answering "the same, ten percent
    faster" by generating again therefore spent minutes to produce the wrong thing —
    generation is SAMPLED, so what came back was another reading, and the take the
    operator had just listened to and liked was gone. Now the raw take is still on disk
    and the answer is one `atempo` away: the same reading, at the pace asked for, in
    about as long as it takes to write the file.

    The word timings are rescaled rather than recovered (:func:`rescale`), so this costs
    no recogniser pass either, and the audio and the captions cannot drift apart —
    `atempo` is a linear map of time, and so is the division.

    None is returned, rather than an error raised, for every reason this cannot be done:
    an engine whose rate IS the synthesis (`edge`, `azure`), a take made before this
    existed and so with no raw beside it, a line whose text or voice has changed since.
    Each of those is a case where re-voicing is the right answer, and the caller's
    fallback is exactly that (see `montage.voice`)."""
    scene = job.scenes[index]
    if scene.unvoiced or not scene.audio:
        return None
    engine = resolve_engine(ctx)
    if varies_rate(engine):
        return None            # the pace is part of the synthesis there, not after it
    take = Path(scene.audio)
    raw = raw_path(take)
    if not raw.is_file():
        return None            # voiced before there was a raw take to keep
    spoken = _spoken(scene.text, _pronounce(ctx))
    try:
        voice = _resolve_voice(ctx, engine, scene.voice)
    except RuntimeError:
        return None            # the voice this line names is no longer resolvable
    words = _cached_words(raw, spoken, voice.cache_key, RAW_RATE, engine)
    if not words:
        return None            # the raw take is not of THIS line any more
    factor = rate_factor(rate_str(int(rate)))
    lay_rate(raw, take, factor)
    scene.tts_rate = int(rate)
    scene.audio = take
    out = rescale(words, factor)
    _store_words(take, spoken, voice.cache_key, rate_str(int(rate)), engine, out)
    scene.words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in out]
    seconds = duration_of(take)
    scene.audio_src_duration = seconds
    if not ctx.is_beats:
        scene.duration = seconds
    log.info("TTS scene %d: re-stretched to %s without voicing it again",
             index, rate_str(int(rate)))
    return seconds


def _run_manual(job: VideoJob, ctx: AppContext) -> None:
    """The operator's own recordings instead of a synthesizer.

    Parks the run until every line has arrived (`ManualVoicePending`, which the
    orchestrator turns into a `paused` checkpoint), then times the words with the
    aligner — a microphone emits no WordBoundary events, and neither does the web
    demo of whatever voice the operator liked enough to do this for."""
    from .. import manual_tts

    table = _pronounce(ctx)
    # BEFORE the wait, not after: an operator who records forty lines and only then
    # learns the recognizer is missing has been made to wait for nothing.
    align_dir = _require_aligner(ctx)
    # …and a pause is not asked for: it is silence the operator already decided the
    # length of, so it is neither in the manifest nor waited for (see `Scene.hush`).
    delivered = manual_tts.collect_or_pause(
        job.workdir, [scene.text for scene in job.scenes],
        skip={i for i, scene in enumerate(job.scenes) if scene.unvoiced},
    )
    offset = 0.0
    total = len(job.scenes)
    for i, scene in enumerate(job.scenes):
        if scene.unvoiced:
            if not ctx.is_beats:
                offset += scene.duration
            ctx.progress("tts", i + 1, total)
            continue
        path = delivered[i]
        spoken = _spoken(scene.text, table)
        src = duration_of(path)
        # the same sidecar cache the synthesized path uses. It earns its keep here for
        # a different reason: nothing is re-recorded on a resume, but everything would
        # be re-ALIGNED, and a recognizer pass per line is not free either.
        raw_words = _cached_words(path, spoken, str(path.name), "manual", "manual")
        if not raw_words:
            raw_words = _as_written(aligner.align(path, spoken, align_dir, src), table)
            _store_words(path, spoken, str(path.name), "manual", "manual", raw_words)
        _finish_scene(scene, path, src, raw_words, ctx, offset)
        if not ctx.is_beats:
            offset += scene.duration
        ctx.progress("tts", i + 1, total)


def _finish_scene(scene, path: Path, src: float, raw_words: list[dict],
                  ctx: AppContext, offset: float) -> None:
    """Write one voiced line back into its scene.

    Drama and info want different things here. In drama one clip per scene is the
    master timeline, so the line's natural length and its SCENE-RELATIVE timings are
    recorded and the footage stage stretches the voice to the clip, finalizing both.
    In info the voice IS the timeline, so the line's length becomes the scene's and
    the timings are moved to their absolute positions immediately."""
    scene.audio = path
    scene.audio_src_duration = src
    if ctx.is_beats:
        scene.words = [Word(text=w["text"], start=w["start"], end=w["end"]) for w in raw_words]
    else:
        scene.duration = src
        scene.words = [
            Word(text=w["text"], start=offset + w["start"], end=offset + w["end"])
            for w in raw_words
        ]


def _voice_card(ctx: AppContext, engine: str):
    """The card the run's voice IS, or None when it is a catalogue voice (or a name
    nothing answers to). Asked before the casting pass, which has nothing to do on a
    voice that is not a card with several recordings in it."""
    name = _voice_name(ctx, engine)
    try:
        found = ctx.store.voice_sample(name) if name else None
    except Exception:  # noqa: BLE001 — a broken card is the speaker's complaint, not ours
        return None
    return found[0] if found is not None else None


def _cast_deliveries(job: VideoJob, ctx: AppContext, engine: str) -> None:
    """Let the writer choose which lines are shouted, before anything is voiced.

    Only ever the EXCEPTIONS, and only into `Scene.voice`: a line it leaves alone keeps
    the empty spec, which means "the card's default" and goes on meaning that after the
    card's default has moved. So this pass never freezes a video onto the recordings it
    happened to pick — it says which lines are the departures from whatever the voice is
    (see `llm/delivery.py` for what the model is and is not allowed to answer).

    It runs on every entry to the stage rather than once, because the thing it reads is
    the script, and the script is what the breakpoint before this lets the operator
    rewrite. An automatic pin on a line that no longer calls for it is therefore taken
    back, while a pin the OPERATOR made is left alone in both directions: not recast, and
    not cleared (`Scene.voice_auto`).

    Silent about everything it cannot do. No card, one recording in it, a catalogue
    engine, no writer available — all of them mean the video is voiced in one delivery,
    which is what it would have been anyway."""
    if not ctx.params.tts_deliveries:
        return
    info = ENGINES.get(engine)
    if info is not None and not info.clones:
        return
    card = _voice_card(ctx, engine)
    if card is None or len(card.sample_names) < 2:
        return
    from ...llm import delivery

    # a pause says nothing, so it is nothing to cast — and it is `fixed` rather than
    # filtered out so the indices the model answers on stay the scenes' own
    fixed = {i for i, sc in enumerate(job.scenes)
             if sc.unvoiced or (sc.voice and not sc.voice_auto)}
    cast = delivery.cast(
        ctx.llm,
        [sc.text for sc in job.scenes],
        card.sample_names,
        card.default_name,
        lambda which: card.samples[which].description,
        fixed=fixed,
        lang=ctx.params.lang,
    )
    changed = 0
    for i, scene in enumerate(job.scenes):
        if i in fixed:
            continue
        want = f"{card.name}:{cast[i]}" if i in cast else ""
        if want != scene.voice:
            changed += 1
        scene.voice = want
        scene.voice_auto = bool(want)
    log.info("delivery: %d line(s) cast away from '%s' (%d changed since last time)",
             len(cast), card.default_name, changed)


def run(job: VideoJob, ctx: AppContext) -> None:
    audio_dir = job.workdir / "tts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    if ctx.params.tts_source == "manual":
        _run_manual(job, ctx)
        return

    # A job with nothing to say needs no synthesizer, and asking for one is not
    # harmless: building a speaker resolves a voice, and an engine that only clones
    # refuses outright when no card was named. That is the right answer for a video
    # whose lines are meant to be spoken and the wrong one for a chat nobody reads
    # aloud, where every line is silent ON PURPOSE (see `Scene.silent`).
    if job.scenes and all(s.unvoiced for s in job.scenes):
        log.info("TTS: nothing to say — every line is silent")
        ctx.progress("tts", len(job.scenes), len(job.scenes))
        return
    speaker = _speaker_for(ctx)
    log.info("TTS: %s · %s", speaker.id, speaker.voice)
    # before the first line is spoken, because the answer decides what is spoken with:
    # a line the writer recasts is voiced with that recording on the first attempt and
    # not re-voiced afterwards
    _cast_deliveries(job, ctx, speaker.id)
    table = _pronounce(ctx)
    offset = 0.0
    total = len(job.scenes)
    for i, scene in enumerate(job.scenes):
        # A pause has nothing to say and a length of its own already (see `Scene.hush`),
        # and so does a line nobody was cast to read (`Scene.silent`). Sent to a
        # synthesizer either would come back as an error or a file of nothing, and
        # either way the length that was set would be overwritten by whatever the
        # engine made of it.
        if scene.unvoiced:
            if not ctx.is_beats:
                offset += scene.duration
            ctx.progress("tts", i + 1, total)
            continue
        path = audio_dir / f"scene_{i:02d}{speaker.suffix}"
        raw_words = _synth_scene(scene, i, path, speaker,
                                 rate_str(_scene_rate(scene, ctx)), table=table)
        _finish_scene(scene, path, duration_of(path), raw_words, ctx, offset)
        if not ctx.is_beats:
            offset += scene.duration
        ctx.progress("tts", i + 1, total)
    # the target duration is a hint for the LLM, not a hard cap — accept whatever came out
