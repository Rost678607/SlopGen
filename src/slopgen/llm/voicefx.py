"""Casting the voice filters: which lines are heard through something other than the room.

The sibling of :mod:`.delivery`, and the two are the same errand asked of the same pass
of reading. That one chooses HOW a line is said out of the recordings a voice card
holds; this one chooses WHAT it is heard through out of the cards in
`configs/voicefx/` — a telephone band, a loudhailer across a square, a projector
rattling behind the words (see :mod:`slopgen.media.voicefx`).

It is worth being clear about what is being asked, because it is narrower than it
sounds. A filter is not a mood and it is not emphasis: it is a statement about WHERE a
line came from. So the only thing the writer is asked to find is lines whose own
wording already says so — a transmission quoted from a radio, an announcement over a
square, a recording somebody is playing back, a sentence that is being remembered
rather than heard. Everything else is the narrator in the room with you, which is what
a video is made of, and that is why the answer is a list of EXCEPTIONS and the empty
list is a perfectly good one.

The three things not left to the model are the three `delivery` keeps:

* **The vocabulary.** Only the cards that exist, by the name they exist under. A name
  it invented is dropped rather than resolved — `media.voicefx` would hear nothing in
  it anyway (`tts.fx_card` returns None for a name nobody answers to), so inventing
  would be a silent no-op rather than an error, which is worse.
* **How many.** A filter says the line came from somewhere else, and a video where a
  quarter of the lines come from somewhere else has no *here* left to come back to.
  The share is capped (:data:`MAX_SHARE`) rather than asked for politely.
* **Whose pins these are.** A line the operator filtered themselves is sent as context
  and never returned as an edit. `Scene.voice_fx_auto` is the flag that says which is
  which, and it is what keeps a re-run from overwriting a human decision.

What the model reads about each card is its `description` — the operator's own sentence
about what it sounds like, or about when to reach for it. That field is the whole of
the interface: a card nobody described is still perfectly usable by hand and simply
never gets chosen here, which is the honest outcome, because a model cannot guess what
«вар3» is supposed to mean.
"""

from __future__ import annotations

import json
import logging
import math

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are deciding which lines of a finished narration are heard THROUGH something "
    "rather than spoken in the room with the listener.\n"
    "The filters you may use are ONLY these:\n{cards}\n"
    "No filter at all is the normal state: it is the narrator's own voice, it is what "
    "this video is made of, and you do NOT return those lines.\n"
    "You get a JSON array of numbered lines in {lang}, in the order they are spoken. "
    'Lines marked "fixed": true were filtered by hand — they are there so you can see '
    "what is around what you are choosing, and you never return them.\n"
    "Rules:\n"
    "- Return a line only when the writing itself says the sound came from somewhere "
    "else: it is quoted from a radio or a telephone, announced over a loudspeaker, "
    "played back off a recording, heard through a wall, or remembered rather than "
    "heard. The words have to carry it.\n"
    "- A line that is merely tense, sad, important, loud or dramatic is NOT filtered. "
    "How a line is said is a different question from where it came from, and this is "
    "not that question.\n"
    "- A filter covers the WHOLE line. If only a few words inside it are the "
    "transmission, leave the line alone.\n"
    "- At most {cap} of these {total} lines may be filtered. Fewer is better: a filter "
    "works by contrast with the lines around it, and a video where they are everywhere "
    "has no ordinary voice left for them to stand out from.\n"
    "- Never invent a filter name and never translate one — copy a name from the list "
    "exactly as it is written there.\n"
    'Respond with JSON only: {{"lines": [{{"i": 12, "through": "<filter name>"}}, ...]}}, '
    "and with an empty list when the narration wants none of this — which is the "
    "ordinary answer for an ordinary video."
)

# The most of a video that may be heard through something. Not taste: a filter is a
# statement about place, and it is READ against the lines around it. At a third of the
# video the thing it is contrasted with is gone, and what is left is a video with an
# inconsistent sound rather than a video with a radio in it.
MAX_SHARE = 0.25

# Lines per request. A whole ordinary video fits in one, which is the point — the
# judgement is about the shape of the narration and not about a line in isolation.
MAX_LINES_PER_CALL = 80


def _catalogue(cards: list[str], describe) -> str:
    """The filters as the prompt lists them: the name the model must copy, and the
    operator's own words about it. The description is where all the information is —
    `плёнка` and `память` mean whatever the person who made those cards meant."""
    out = []
    for name in cards:
        note = (describe(name) or "").strip()
        out.append(f"- {name}" + (f" — {note}" if note else ""))
    return "\n".join(out)


def cast(llm, lines: list[str], cards: list[str], describe,
         fixed: set[int] | None = None, lang: str = "ru") -> dict[int, str]:
    """Which lines are heard through which filter, as {line index: card name}.

    Only the exceptions are in there, and only ever names out of `cards`. Never raises:
    a run whose writer is unavailable, slow or uncooperative is a run heard entirely in
    the narrator's own voice, which is exactly what it would have been without this.
    """
    fixed = fixed or set()
    if not cards or not lines:
        return {}
    cap = max(1, math.floor(len(lines) * MAX_SHARE))
    system = _SYSTEM.format(cards=_catalogue(cards, describe), lang=lang,
                            cap=cap, total=len(lines))
    out: dict[int, str] = {}
    for start in range(0, len(lines), MAX_LINES_PER_CALL):
        batch = range(start, min(start + MAX_LINES_PER_CALL, len(lines)))
        payload = [
            {"i": i, "text": lines[i], **({"fixed": True} if i in fixed else {})}
            for i in batch if lines[i].strip()
        ]
        if not payload:
            continue
        try:
            data = llm.complete_json(
                "tts_voicefx", system,
                json.dumps(payload, ensure_ascii=False, indent=1))
        except Exception as e:  # noqa: BLE001 — no filtering is a complete answer
            log.warning("voice-filter casting failed for %d line(s) (%s)", len(payload), e)
            continue
        for item in data.get("lines") or []:
            if not isinstance(item, dict):
                continue
            i, which = item.get("i"), str(item.get("through", "")).strip()
            if not isinstance(i, int) or i not in batch or i in fixed:
                continue
            if which not in cards:
                if which:
                    log.warning("the writer asked to hear line %d through %r, which is "
                                "not a filter in this base — it keeps its own voice",
                                i, which)
                continue
            out[i] = which
    if len(out) > cap:
        # Earliest wins, for the reason `delivery.cast` gives: the cap is a property of
        # the whole video and some rule has to be deterministic. Here it also happens to
        # be the kinder one — the first radio transmission is the one that establishes
        # that this video has a radio in it.
        kept = dict(sorted(out.items())[:cap])
        log.info("voice filters: the writer picked %d of %d lines, keeping the first %d",
                 len(out), len(lines), cap)
        return kept
    return out
