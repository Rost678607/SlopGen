"""Word timings for the engines that give none.

This is **not** transcription. The words are already known — they are the script —
so the recognizer is only ever asked one thing: when was each one said. That changes
what "accuracy" means here. A recognizer that mishears «сургучные» as «сургучный»
still put it in the right place, and the script's spelling wins; a recognizer that
drops a word entirely costs nothing but that word's boundary, which is interpolated
from its neighbours. So a 46 MiB small model is enough, and a big one would buy
almost nothing.

The alignment itself is Levenshtein over words — the same edit distance
`stages/tts._as_written` walks when it merges a respelling back into one word, and
the same shape as `stages/subtitles._retime`'s fallback for when the words moved.
Three cases come out of it:

* **matched or substituted** — the script word takes the heard word's span, exactly.
* **heard but not in the script** (the recognizer split one word into two) — the
  extra span is absorbed into the word before it, rather than dropped, so the line
  keeps covering all of its own audio.
* **in the script but not heard** — a run of unheard words is spread evenly across
  the silence between its heard neighbours. Approximate, and confined to exactly the
  words that were missed.

Nothing recognized at all (a wrong-language model, a silent file) falls back to
spreading the line over its own duration by word length, which is what `_retime`
does for a rewritten line and is a good deal better than no subtitles.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import wave
from pathlib import Path

from .base import Timing

log = logging.getLogger(__name__)

_PUNCT = "«»\"'“”„.,!?;:—–-…()"
SAMPLE_RATE = 16000  # what vosk's small models are trained on

# The recognizer reports a word as starting slightly after it acoustically does — it
# needs a few frames of evidence before it commits. Measured against edge-tts' own
# WordBoundary events, which are ground truth because the synthesizer emitted both the
# audio and the timing: 96 words, two voices, four lines, median lag +0.071s with a
# standard deviation of 0.046s. Constant across voices and lines, so it is a bias
# rather than noise, and subtracting it puts the residual inside one frame. Applied to
# start and end alike, which slides each word without changing how long it lasts.
LAG_S = 0.071

_MODELS: dict[str, object] = {}  # loaded vosk models, by folder — loading costs ~1s


def _bare(token: str) -> str:
    return token.strip(_PUNCT).casefold().replace("ё", "е")


def _to_wav16k(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-ar", str(SAMPLE_RATE), "-ac", "1", str(dst)],
        check=True, capture_output=True,
    )


def _load_model(model_dir: Path):
    key = str(model_dir)
    if key not in _MODELS:
        from vosk import Model, SetLogLevel

        SetLogLevel(-1)  # the Kaldi banner is not ours to print
        _MODELS[key] = Model(str(model_dir))
    return _MODELS[key]


def recognize(audio: Path, model_dir: Path) -> list[Timing]:
    """Heard words with their spans, straight from the recognizer."""
    from vosk import KaldiRecognizer

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "align.wav"
        _to_wav16k(audio, wav)
        rec = KaldiRecognizer(_load_model(model_dir), SAMPLE_RATE)
        rec.SetWords(True)
        heard: list[dict] = []
        with wave.open(str(wav), "rb") as wf:
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    heard += json.loads(rec.Result()).get("result", [])
        heard += json.loads(rec.FinalResult()).get("result", [])
    return [{"text": w["word"],
             "start": max(0.0, float(w["start"]) - LAG_S),
             "end": max(0.0, float(w["end"]) - LAG_S)}
            for w in heard]


# A word the recognizer substituted is not necessarily a word it failed to hear.
# On this project's own fandom scripts the vocabulary is invented — «Тлень»,
# «Парёнка», «пропар» — and no Russian model has ever met any of it, so it spells
# what it heard with the nearest real word: «тлени» comes back as «тления». That is
# the right word, in the right place, from a recognizer doing its job. Counting only
# exact hits scores such a line at a fifth of itself and calls a perfectly good take
# a recitation of the reference, which is what `_script_span` is deciding.
#
# So a substitution counts for the script when the two are the same word to within a
# few letters — the same edit distance this module already walks over tokens, walked
# over characters instead. 0.6 keeps «тлени»/«тления» (0.83) and rejects
# «тлени»/«мы» (0.0); a one-letter word can never clear it, which is deliberate,
# since «в» and «и» differ by one letter and by everything else.
SAME_WORD = 0.6


def _edit(a: str, b: str) -> int:
    """Levenshtein distance, two rows and no backtrace — comparing two words needs
    only the number. Called for every pair of words in a line by `_sub_cost`, which
    is why it keeps no table."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _same_word(a: str, b: str) -> bool:
    longest = max(len(a), len(b))
    return longest > 0 and 1.0 - _edit(a, b) / longest >= SAME_WORD


# Word-level costs, in hundredths so the whole table stays integer and the backtrace
# can compare exactly. A dropped or invented word costs GAP; a substitution costs
# between 1 and GAP according to how far the two words are from each other as
# strings. That grading is the point: with a flat cost of 1 for every substitution,
# every alignment of the same length ties, and which one the backtrace happens to
# return is decided by the order the branches are tested in. Measured on a real take
# — script «в тлени водицу горячую запретили», heard «я же к я упал реку спасибо мы
# клине водить у горячий» — the flat cost put «водицу» against «мы» and left
# «водить» to «запретили», scoring a line that IS half-present at zero. Graded, the
# near-spellings snap onto each other, which is what both the timings and the
# recitation verdict are read off.
GAP_COST = 100


def _sub_cost(a: str, b: str) -> int:
    if a == b:
        return 0
    longest = max(len(a), len(b))
    return max(1, min(GAP_COST, round(GAP_COST * _edit(a, b) / longest)))


def _ops(ref: list[str], hyp: list[str]) -> list[tuple[str, int, int]]:
    """Levenshtein backtrace as ("match"|"sub"|"del"|"ins", ref_index, hyp_index)."""
    n, m = len(ref), len(hyp)
    cost = [[_sub_cost(a, b) for b in hyp] for a in ref]
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i * GAP_COST
    for j in range(m + 1):
        d[0][j] = j * GAP_COST
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + GAP_COST, d[i][j - 1] + GAP_COST,
                          d[i - 1][j - 1] + cost[i - 1][j - 1])
    out: list[tuple[str, int, int]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            c = cost[i - 1][j - 1]
            if d[i][j] == d[i - 1][j - 1] + c:
                out.append(("match" if not c else "sub", i - 1, j - 1))
                i, j = i - 1, j - 1
                continue
        if i > 0 and d[i][j] == d[i - 1][j] + GAP_COST:
            out.append(("del", i - 1, -1))
            i -= 1
            continue
        out.append(("ins", -1, j - 1))
        j -= 1
    out.reverse()
    return out


def _spread(tokens: list[str], start: float, end: float) -> list[Timing]:
    """Divide a span among words by length — the last resort, and the same rule
    `subtitles._retime` uses when a rewrite changed the word count."""
    weights = [len(t) + 1 for t in tokens]
    total = sum(weights) or 1
    at = start
    out: list[Timing] = []
    for t, w in zip(tokens, weights):
        share = (end - start) * w / total
        out.append({"text": t, "start": at, "end": at + share})
        at += share
    return out


def place(text: str, heard: list[Timing], duration: float) -> list[Timing]:
    """Put the SCRIPT's words on the timeline the recognizer heard.

    Pure function of its arguments, so it is testable without a model and without
    audio — which matters, because this is where the subtitles' accuracy is decided."""
    tokens = text.split()
    if not tokens:
        return []
    if not heard:
        return _spread(tokens, 0.0, duration)

    ref = [_bare(t) for t in tokens]
    hyp = [_bare(w["text"]) for w in heard]
    spans: list[list[float] | None] = [None] * len(tokens)
    last_ref = -1
    for op, i, j in _ops(ref, hyp):
        if op in ("match", "sub"):
            spans[i] = [heard[j]["start"], heard[j]["end"]]
            last_ref = i
        elif op == "ins" and last_ref >= 0 and spans[last_ref]:
            # the recognizer heard more words than the script has here — the audio is
            # still the script's, so widen the word before rather than lose the time
            spans[last_ref][1] = max(spans[last_ref][1], heard[j]["end"])

    # fill the words nobody heard, evenly, inside the hole they left
    i = 0
    while i < len(spans):
        if spans[i] is not None:
            i += 1
            continue
        run_end = i
        while run_end < len(spans) and spans[run_end] is None:
            run_end += 1
        left = spans[i - 1][1] if i > 0 and spans[i - 1] else 0.0
        right = spans[run_end][0] if run_end < len(spans) and spans[run_end] else max(duration, left)
        if right <= left:
            right = left + 0.12 * (run_end - i)  # nothing to borrow from: nominal
        step = (right - left) / (run_end - i)
        for k in range(i, run_end):
            spans[k] = [left + step * (k - i), left + step * (k - i + 1)]
        i = run_end

    out: list[Timing] = []
    prev_end = 0.0
    for token, span in zip(tokens, spans):
        start, end = span  # type: ignore[misc]
        start = max(start, prev_end)
        end = max(end, start + 0.02)
        out.append({"text": token, "start": start, "end": end})
        prev_end = end
    return out


# Surplus speech shorter than this is recogniser noise, not a leak worth cutting for.
MIN_SURPLUS_S = 0.35
CLIP_PAD_S = 0.12  # kept around the script's own speech, so nothing is cut mid-breath


def _script_span(text: str, heard: list[Timing]) -> tuple[float | None, float | None, list[str], float]:
    """Where in the take the SCRIPT is, and what was said outside it.

    A cloning model is given the reference transcript as an example and is meant to
    stop after the line it was asked for. It does not always: measured here, takes
    come back with a word glued to the front, or with a piece of the reference
    transcript recited after the line has ended. The recogniser already knows which
    heard words correspond to script words — that is what the alignment is — so the
    ones before the first match and after the last are, by construction, not this
    line."""
    if not heard:
        return None, None, [], 0.0
    ref = [_bare(t) for t in text.split()]
    hyp = [_bare(w["text"]) for w in heard]
    ops = _ops(ref, hyp)
    matched = [j for op, _i, j in ops if op in ("match", "sub")]
    if not matched:
        return None, None, [w["text"] for w in heard], 0.0
    first, last = min(matched), max(matched)
    surplus = [w["text"] for k, w in enumerate(heard) if k < first or k > last]
    # How much of the line is in there, measured in LETTERS over the span the line was
    # found in — not in whole words matched. Counting words was tried and cannot do
    # this job: on a made-up vocabulary the recognizer half-spells everything, so a
    # flawless take of «В Тлени водицу горячую запретили» comes back as «вот линии
    # водиться горячую запретили» and scores 2 words out of 5, while a take reciting
    # the reference can score 2 out of 5 as well. Measured over 24 takes from two
    # references, the word count leaves the good and the recited overlapping (good
    # 0.22-1.00 against recited 0.00-0.44) and the letters separate them cleanly
    # (0.53-0.94 against 0.06-0.44). A half-heard word is most of its letters; a
    # different word is not.
    return (heard[first]["start"], heard[last]["end"], surplus,
            _closeness(" ".join(ref), " ".join(hyp[first:last + 1])))


def _closeness(a: str, b: str) -> float:
    longest = max(len(a), len(b))
    return max(0.0, 1.0 - _edit(a, b) / longest) if longest else 0.0


def clip_to_script(audio: Path, text: str, model_dir: Path,
                   duration: float) -> tuple[list[Timing], float, float]:
    """Timings for `text`, with anything the take says OUTSIDE the script cut away.

    Returns the timings, the take's new length, and how much of the script is
    actually IN it (0..1, see `_script_span`) — because cutting cannot save a take
    that never said the line at all. Measured here: asked for "Он закрыл окно и сел за стол", one
    take came back saying "как я упал в реку спасибо мы живём", which is the reference
    and nothing else. Its length was within 8% of the estimate, so length alone called
    it good; the fraction of script words in it was zero.

    Cutting rather than rejecting is
    the right remedy because the line itself is usually fine — it is bracketed by
    speech that does not belong to it — and because a re-roll of a local take costs
    minutes."""
    try:
        heard = recognize(audio, model_dir)
    except Exception as e:  # noqa: BLE001 — see `align`
        log.warning("alignment failed (%s: %s)", type(e).__name__, e)
        return place(text, [], duration), duration, 1.0  # unmeasured, so not accused

    start, end, surplus, matched = _script_span(text, heard)
    if start is None or end is None:
        return place(text, heard, duration), duration, matched
    lead, tail = start, duration - end
    if not surplus or (lead + tail) < MIN_SURPLUS_S:
        return place(text, heard, duration), duration, matched

    lo = max(0.0, start - CLIP_PAD_S)
    hi = min(duration, end + CLIP_PAD_S)
    log.warning("take says %d word(s) that are not in the line (%s) — cutting to %.2f-%.2fs",
                len(surplus), " ".join(surplus)[:60], lo, hi)
    if not _cut(audio, lo, hi):
        return place(text, heard, duration), duration, matched
    inside = [{"text": w["text"], "start": w["start"] - lo, "end": w["end"] - lo}
              for w in heard if w["start"] >= start - 1e-3 and w["end"] <= end + 1e-3]
    return place(text, inside, hi - lo), hi - lo, matched


def _cut(audio: Path, start: float, end: float) -> bool:
    """Keep only `start`..`end` of the file, in place."""
    tmp = audio.with_name(audio.name + ".clip" + audio.suffix)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-ss", f"{start:.3f}",
             "-to", f"{end:.3f}", str(tmp)],
            check=True, capture_output=True,
        )
        tmp.replace(audio)
        return True
    except (subprocess.CalledProcessError, OSError):
        tmp.unlink(missing_ok=True)
        return False


def align(audio: Path, text: str, model_dir: Path, duration: float) -> list[Timing]:
    """Timings for `text` as spoken in `audio`. Never raises for a recognition that
    went badly — a bad alignment still produces subtitles, while an exception here
    would lose a synthesis that already cost minutes of CPU."""
    try:
        heard = recognize(audio, model_dir)
    except Exception as e:  # noqa: BLE001 — see docstring
        log.warning("alignment failed (%s: %s) — spreading words by length",
                    type(e).__name__, e)
        heard = []
    if not heard:
        log.warning("aligner heard nothing in %s — spreading words by length", audio.name)
    return place(text, heard, duration)


def model_for(ctx_lang: str, cfg) -> str:
    """The recognizer id this run's language needs, per `[tts.align].models`."""
    return cfg.align.models.get(ctx_lang) or cfg.align.models.get("en", "vosk-en-small")
