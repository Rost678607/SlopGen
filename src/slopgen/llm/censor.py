"""Cleaning the SUBTITLES only: the voice keeps every word, the on-screen text does not.

Social platforms moderate what they can read, and burned-in subtitles are readable.
This rewrites the profane lines into harmless ones while the audio plays on untouched
— including words that merely *look* profane, such as the first part of the name
"Хуй Сунь Вынь", which trips a filter exactly the same way.

Rewriting happens a LINE at a time, not a word at a time. Swapping single words leaves
the sentence limping ("Уйдите нахуй с моей пары" is no better and reads worse), so the
model gets the whole line and returns a whole line:

    Съебал нахуй с моей пары пидорас блять
    → Уйдите пожалуйста с моей пары молодой человек

That means the word count changes, and subtitles are timed per word — so the caller
re-spreads the line's original span over the new words (see stages/subtitles.py).
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are rewriting subtitle lines so they do not trip social-network moderation. "
    "The audio is NOT changed — only the text burned onto the picture.\n"
    "You get a JSON array of lines, in {lang}, in the order they are spoken. Return the "
    "SAME number of lines, in the same order.\n"
    "Rewrite a line ONLY if it contains profanity, obscenity, or anything a filter would "
    "read as profane — including when it is buried inside a longer word or a person's NAME "
    "(«Хуй Сунь Вынь» must lose that first word even though it is somebody's name). Every "
    "other line comes back BYTE-IDENTICAL: do not fix spelling, punctuation, casing or "
    "style, and do not translate.\n"
    "A rewritten line must read as a natural, coherent sentence a person would say — not "
    "the original with holes punched in it. Keep what the line MEANS and who it is aimed "
    "at, shift only the register, and keep it about as long as the original (it has to be "
    "read in the same breath).\n"
    "Example: «Съебал нахуй с моей пары пидорас блять» → «Уйдите пожалуйста с моей пары "
    "молодой человек».\n"
    'Respond with JSON only: {{"lines": ["...", ...]}}.'
)

# Word stems used to decide which lines need the model at all, and by the fallback
# mask. Deliberately broader than the script stage's list: this side also has to
# catch profanity buried inside a name.
_STEMS = re.compile(
    r"бля|хуй|хуё|хуе|хую|хуя|пизд|ёб|ъеб|ьеб|\bеб|заеб|наеб|уеб|сук[аи]|мудак|мудил|залуп|дроч"
    r"|пидор|пидар|говн|срал|срать|ссан"
    r"|\bfuck|\bshit|\bcunt|\bbitch|\bbastard|\basshole|\bdick\b|\bwhore\b",
    re.IGNORECASE,
)


def looks_profane(text: str) -> bool:
    return bool(_STEMS.search(text))


def _mask_word(word: str) -> str:
    """Fallback for one word: keep the first letter, blank the rest."""
    if not looks_profane(word):
        return word
    tail = re.search(r"[.,!?;:—…]+$", word)
    core = word[: tail.start()] if tail else word
    return (core[:1] + "*" * max(len(core) - 1, 1)) + (tail.group(0) if tail else "")


def mask_line(line: str) -> str:
    """Fallback for a whole line, used when the model is unavailable or uncooperative.
    Cruder than a rewrite, but it never changes the word count and never fails."""
    return " ".join(_mask_word(w) for w in line.split())


def clean_lines(llm, lines: list[str], lang: str = "ru") -> list[str]:
    """Rewrite the profane lines and return all of them, in order. Never raises: a
    failed or malformed response falls back to masking, so a run cannot die here."""
    flagged = [i for i, line in enumerate(lines) if looks_profane(line)]
    if not flagged:
        return list(lines)
    try:
        data = llm.complete_json(
            "subtitle_clean", _SYSTEM.format(lang=lang),
            json.dumps(lines, ensure_ascii=False, indent=1),
        )
        out = data.get("lines")
        if isinstance(out, list) and len(out) == len(lines):
            # only the flagged lines may change: a model that also "improved" a clean
            # line elsewhere would silently desync those subtitles from the speech
            clean = [
                str(new).strip() if i in set(flagged) and str(new).strip() else old
                for i, (old, new) in enumerate(zip(lines, out))
            ]
            # a rewrite that kept the profanity is no rewrite
            return [
                mask_line(line) if looks_profane(line) else line for line in clean
            ]
        log.warning("subtitle cleaning: model returned %s lines for %d — masking instead",
                    len(out) if isinstance(out, list) else "no", len(lines))
    except Exception as e:
        log.warning("subtitle cleaning failed (%s) — masking instead", e)
    return [mask_line(line) if looks_profane(line) else line for line in lines]
