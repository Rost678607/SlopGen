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
    'You get a JSON array of numbered lines in {lang}, in the order they are spoken. Each '
    'carries "edit": true or false. Rewrite ONLY the `edit: true` ones. The others are '
    "there so you can see the surrounding conversation — never return them.\n"
    "A rewritten line must read as a natural, coherent sentence a person would actually "
    "say — not the original with holes punched in it. Keep what the line MEANS and who it "
    "is aimed at, shift only the register, and keep it about as long as the original (it "
    "has to be read in the same breath). Do not translate.\n"
    "Remove every profanity and obscenity, including profanity buried inside a longer word "
    "or a person's NAME («Хуй Сунь Вынь» must lose that first word even though it is "
    "somebody's name).\n"
    "Example: «Съебал нахуй с моей пары пидорас блять» → «Уйдите пожалуйста с моей пары "
    "молодой человек».\n"
    'Respond with JSON only, one entry per line you rewrote, keeping its number: '
    '{{"lines": [{{"i": 3, "text": "..."}}, ...]}}.'
)

# Lines sent per request. One request per video is the normal case — the cap only
# splits a very long, very foul script, where a single huge prompt is both likelier
# to drift and costlier to lose.
MAX_LINES_PER_CALL = 40
CONTEXT_RADIUS = 1  # neighbours sent (read-only) around each line being rewritten

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


def _batches(flagged: list[int], total: int) -> list[tuple[list[int], list[int]]]:
    """Group the lines to rewrite into requests, each as (indices_to_edit, indices_sent).

    Sending the whole script would trebles the tokens for a script that is a third
    profane, so only the flagged lines travel — each with its immediate neighbours,
    unedited, so the model can hear the conversation around what it is rewriting."""
    out: list[tuple[list[int], list[int]]] = []
    for start in range(0, len(flagged), MAX_LINES_PER_CALL):
        edit = flagged[start:start + MAX_LINES_PER_CALL]
        sent = sorted({
            j for i in edit
            for j in range(max(i - CONTEXT_RADIUS, 0), min(i + CONTEXT_RADIUS + 1, total))
        })
        out.append((edit, sent))
    return out


def clean_lines(llm, lines: list[str], lang: str = "ru") -> list[str]:
    """Rewrite the profane lines and return all of them, in order.

    Never raises, and never loses more than it has to: replies are keyed by line
    number, so a partial or reordered response still lands the lines it did return
    and only the rest fall back to masking."""
    flagged = [i for i, line in enumerate(lines) if looks_profane(line)]
    if not flagged:
        return list(lines)  # nothing to clean — no request at all

    out = list(lines)
    system = _SYSTEM.format(lang=lang)
    for edit, sent in _batches(flagged, len(lines)):
        payload = [
            {"i": i, "edit": i in set(edit), "text": lines[i]} for i in sent
        ]
        try:
            data = llm.complete_json(
                "subtitle_clean", system, json.dumps(payload, ensure_ascii=False, indent=1)
            )
            for item in data.get("lines") or []:
                if not isinstance(item, dict):
                    continue
                i, text = item.get("i"), str(item.get("text", "")).strip()
                # only a line we asked about may change: a model that also "improved"
                # a context line would silently desync those subtitles from the speech
                if isinstance(i, int) and i in set(edit) and text:
                    out[i] = text
        except Exception as e:
            log.warning("subtitle cleaning failed for %d line(s) (%s)", len(edit), e)

    # whatever came back still profane, or never came back at all, gets masked
    return [mask_line(line) if looks_profane(line) else line for line in out]
