"""What an info clip is about, in one sentence.

The stage that opens a run asks this when the operator left the idea blank; the
wizard asks it before there is a run, so the operator can see the topic and change
it rather than find out afterwards what the video turned out to be about. One
prompt for both — a topic the wizard proposes and a topic the run invents should
not be two different kinds of sentence."""

from __future__ import annotations

from collections.abc import Iterable

LANG_NAMES = {"en": "English", "ru": "Russian"}

SYSTEM = (
    "You invent topics for viral vertical short videos (YouTube Shorts). "
    'Respond with JSON only: {"topic": "<one topic, a single sentence>"}. '
    "The topic must be specific and hooky, not generic."
)

# Told what to do, the model must do THAT and not wander off into a topic it likes
# better; left free, it picks. The difference is worth spelling out, because an
# instruction quietly ignored looks exactly like an instruction that did nothing.
_TOLD = "Follow the operator's instruction exactly, even if you would choose otherwise."
_REWRITE = "Rewrite the current topic to satisfy the instruction; keep what it does not touch."


def write_topic(llm, lang: str = "en", brief: str = "", recent: Iterable[str] = (),
                current: str = "", instruction: str = "") -> str:
    """Propose, or rewrite, the topic of one info clip.

    `brief` is the niche the content type describes, empty for "auto" — with none,
    the model may pick anything. `recent` are topics already made in this niche and
    language, which it is told not to repeat; that is the whole of the de-duplication
    and it is why the caller reads history rather than trusting the model's memory."""
    used = [t for t in (str(x).strip() for x in recent) if t]
    system = SYSTEM
    if instruction.strip():
        system += " " + (_REWRITE if current.strip() else _TOLD)
    user = f"Niche brief: {brief}\n\n" if brief else ""
    user += f"Write the topic in {LANG_NAMES.get(lang, lang)}."
    if current.strip():
        user += f"\n\nCurrent topic: {current.strip()}"
    if instruction.strip():
        user += f"\n\nOperator instruction: {instruction.strip()}"
    if used:
        user += "\n\nDo NOT repeat or paraphrase these already-used topics:\n- " + "\n- ".join(used)
    return str(llm.complete_json("idea", system, user).get("topic", "")).strip()
