"""Cleaning the SUBTITLES only: the voice keeps every word, the on-screen text does not.

Social platforms moderate what they can read, and burned-in subtitles are readable.
This swaps each profane word for a harmless one of similar meaning while the audio
plays on untouched — including words that merely *look* profane, such as the first
part of the name "Хуй Сунь Вынь", which trips a filter exactly the same way.

The replacement is strictly word-for-word: subtitles are timed per word, so the
returned list must line up one-to-one with the input or the timing falls apart.
A model that returns anything else is discarded in favour of the regex mask.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are sanitising subtitle text so it does not trip social-network moderation. "
    "You get a JSON array of words exactly as they will appear on screen, one per "
    "subtitle slot, in {lang}.\n"
    "Return the SAME array — identical length, identical order. Replace ONLY the words "
    "that are profane, obscene, or would read as profane to a filter (including when the "
    "profanity is merely part of a longer word or a person's NAME), each with a harmless "
    "word that keeps the sentence readable and, where possible, its meaning and register. "
    "Match the original's length and grammatical form as closely as you can, and keep any "
    "trailing punctuation of the word you replace.\n"
    "Every other word must come back BYTE-IDENTICAL — do not fix spelling, casing or "
    "grammar, and never merge or split words.\n"
    'Respond with JSON only: {{"words": ["...", ...]}}.'
)

# Word stems used by the fallback mask and to decide whether a call is needed at all.
# Deliberately broader than the script stage's list: this side also has to catch
# profanity buried inside a name.
_STEMS = re.compile(
    r"бля|хуй|хуё|хуе|хую|хуя|пизд|ёб|еб|заеб|наеб|сук[аи]|мудак|мудил|залуп|дроч|пидор|пидар"
    r"|\bfuck|\bshit|\bcunt|\bbitch|\bbastard|\basshole|\bdick\b|\bwhore\b",
    re.IGNORECASE,
)


def looks_profane(text: str) -> bool:
    return bool(_STEMS.search(text))


def mask(word: str) -> str:
    """Fallback when the model is unavailable or uncooperative: keep the first letter
    and blank the rest, so the line still reads as a word without spelling it out."""
    if not looks_profane(word):
        return word
    tail = re.search(r"[.,!?;:—…]+$", word)
    core = word[: tail.start()] if tail else word
    return (core[:1] + "*" * max(len(core) - 1, 1)) + (tail.group(0) if tail else "")


def clean_words(llm, words: list[str], lang: str = "ru") -> list[str]:
    """Return the subtitle words with profanity replaced. Never raises and never
    changes the word count — the caller's timings depend on both."""
    if not words or not any(looks_profane(w) for w in words):
        return list(words)
    try:
        data = llm.complete_json(
            "subtitle_clean", _SYSTEM.format(lang=lang),
            json.dumps(words, ensure_ascii=False),
        )
        out = data.get("words")
        if isinstance(out, list) and len(out) == len(words):
            # a model that "cleaned" a clean word is ignored for that word: only the
            # ones we flagged may change, so an unrelated rewrite cannot slip through
            return [
                str(new) if looks_profane(old) else old
                for old, new in zip(words, out)
            ]
        log.warning("subtitle cleaning: model returned %s words for %d — falling back to masking",
                    len(out) if isinstance(out, list) else "no", len(words))
    except Exception as e:
        log.warning("subtitle cleaning failed (%s) — falling back to masking", e)
    return [mask(w) for w in words]
