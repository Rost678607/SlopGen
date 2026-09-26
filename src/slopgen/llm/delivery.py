"""Casting the intonations: which lines are said with which take of the voice.

A cloned voice has no parameter for anger or for a whisper — it has recordings, and a
card holds several of them with one named as the default (see
`config.models.VoiceConfig`). Everything the operator needed to do by hand at the
voiceover breakpoint was therefore the same small judgement, forty times: this line is
a shout, that one is an aside, the rest are the video.

So the writer is asked once, with the whole script in front of it and the card's own
deliveries as the only vocabulary it may use. It answers with the exceptions — the
lines that should NOT come out in the default delivery — and nothing else, because the
default is what a line means by saying nothing (`Scene.voice = ""` keeps following the
card, so moving the card's pointer afterwards still moves those lines).

Three things are deliberately not left to the model:

* **The vocabulary.** A name it did not get is dropped rather than resolved; there is
  no delivery to be had that nobody recorded, and «шёпот» invented by a model is a
  `ConfigError` an hour later at synthesis time.
* **How many.** Timbre travels with the delivery — two takes of one person recorded on
  different days clone as slightly different people — so a video that switches every
  third line does not sound expressive, it sounds like two narrators. The share is
  capped here (:data:`MAX_SHARE`) rather than asked for politely in the prompt.
* **Whose pins these are.** A line the operator pinned themselves is sent as context
  and never returned as an edit. Its `Scene.voice_auto` is False, and that flag is
  what keeps a re-run from overwriting a human decision with a fresh guess.
"""

from __future__ import annotations

import json
import logging
import math

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are casting the READING of a narration that has already been written. The "
    "voice is one person, recorded several times in different manners, and you choose "
    "which recording says which line.\n"
    "The deliveries you may use are ONLY these:\n{deliveries}\n"
    "'{default}' is the default one. It is the voice of this video: most lines are said "
    "with it, and you do NOT return those.\n"
    "You get a JSON array of numbered lines in {lang}, in the order they are spoken. "
    'Lines marked "fixed": true were cast by hand — they are there so you can see the '
    "reading around what you are choosing, and you never return them.\n"
    "Rules:\n"
    "- Return a line only when the writing plainly calls for another delivery — it is "
    "shouted, whispered, muttered, said through tears. A line that is merely tense, "
    "important or dramatic is said in the default delivery.\n"
    "- A delivery covers the WHOLE line, because it is one recording. If only a couple "
    "of words inside the line are shouted, leave the line alone.\n"
    "- At most {cap} of these {total} lines may be recast. Fewer is better. Each switch "
    "is audibly a different take of the same person, so a video that keeps switching "
    "sounds like it changed narrator rather than changed tone.\n"
    "- Never invent a delivery name, never translate one, and never return the default "
    "one — copy a name from the list exactly as it is written there.\n"
    'Respond with JSON only: {{"lines": [{{"i": 12, "as": "<delivery name>"}}, ...]}}, '
    "and with an empty list when the narration wants none of this."
)

# The most a card's non-default deliveries may cover, as a share of the lines. Not a
# style opinion: the takes are separate recordings of one person and clone as slightly
# different people, so switching is a cost that grows with how often it happens.
MAX_SHARE = 0.34

# Lines per request. A whole ordinary video fits in one, which is the point — the
# judgement is about the shape of the narration and not about a line in isolation, so
# splitting it is a last resort for a script far longer than anything here makes.
MAX_LINES_PER_CALL = 80


def _catalogue(names: list[str], describe) -> str:
    """The deliveries as the prompt lists them: the name the model must copy, and the
    operator's own words about it when they wrote any. The description is where the
    real information is — `зло` and `тише` mean whatever the person who cut those takes
    meant by them."""
    out = []
    for which in names:
        note = (describe(which) or "").strip()
        out.append(f"- {which}" + (f" — {note}" if note else ""))
    return "\n".join(out)


def cast(llm, lines: list[str], names: list[str], default: str, describe,
         fixed: set[int] | None = None, lang: str = "ru") -> dict[int, str]:
    """Which lines get which delivery, as {line index: delivery name}.

    Only the exceptions are in there, and only ever names out of `names`. Never raises:
    a run whose writer is unavailable, slow or uncooperative is a run voiced entirely in
    the card's default delivery, which is exactly what it would have been without this.
    """
    fixed = fixed or set()
    choices = [n for n in names if n != default]
    if not choices or not lines:
        return {}
    cap = max(1, math.floor(len(lines) * MAX_SHARE))
    system = _SYSTEM.format(deliveries=_catalogue(names, describe), default=default,
                            lang=lang, cap=cap, total=len(lines))
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
                "tts_delivery", system,
                json.dumps(payload, ensure_ascii=False, indent=1))
        except Exception as e:  # noqa: BLE001 — no casting is a complete answer
            log.warning("delivery casting failed for %d line(s) (%s)", len(payload), e)
            continue
        for item in data.get("lines") or []:
            if not isinstance(item, dict):
                continue
            i, which = item.get("i"), str(item.get("as", "")).strip()
            if not isinstance(i, int) or i not in batch or i in fixed:
                continue
            if which not in choices:
                # the default is a no-op said out loud, and anything else is a name
                # nobody recorded — both are dropped, and only the second is worth a line
                # in the log
                if which and which != default:
                    log.warning("the writer asked for delivery %r, which this card has "
                                "no recording of — line %d keeps the default", which, i)
                continue
            out[i] = which
    if len(out) > cap:
        # Earliest wins. Not because the early lines matter more, but because the cap is
        # a property of the whole video and SOME rule has to be deterministic: a delivery
        # that lands once, late, with nothing around it reads as a slip rather than as a
        # choice.
        kept = dict(sorted(out.items())[:cap])
        log.info("delivery: the writer recast %d of %d lines, keeping the first %d",
                 len(out), len(lines), cap)
        return kept
    return out
