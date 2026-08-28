"""How long should this be, when nobody said.

`duration_s = 0` means the operator did not buy a length: the model decides it, from
the material. That is one behaviour with two implementations, and the split is not a
compromise — it is what the modes actually need.

The **info** clip needs no call at all. Its script is a single request and the finished
video is exactly as long as the narration turned out to be, so nothing in the pipeline
ever has to know the number: the writer is simply told to choose one (see
`stages/script.SYSTEM`).

The **beat** modes cannot do that. A drama or a fandom video is cut into shots BEFORE a
word is written — the length is what `pipeline/drama.plan_slots` divides into slots, the
slots decide how many beats there are, and the beats decide how many windows the script
is written in. So the length has to exist first, and that is what this module is for:
one small call that reads the brief and answers with seconds.

The one number it is given that it could not work out for itself is how long the brief
would take to SAY. That is the whole question for a brief that is already a finished
text — six rules and three accounts, written to be read out, is as long as reading it
out takes — while a brief that merely names a subject says nothing about length at all.
Handing over both the text and its spoken length lets the model tell those two cases
apart instead of guessing at a round number.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# The range a vertical video may land in when nobody bought a length. The floor is
# about where a piece stops being able to say anything; the ceiling is a long-form
# drama, and is a guard against a model that reads "as long as the material needs" as
# permission to plan an hour — 900 seconds is already 150 shots to make.
MIN_FREE_S, MAX_FREE_S = 15.0, 900.0

# What the run falls back to when the call fails or comes back unusable. A free length
# must never collapse to zero: `plan_slots` would cut a zero budget into ONE slot and
# the operator would get a one-beat video with no error anywhere.
DEFAULT_FREE_S = 120.0

# What is being written, per mode (`beats.Writer.kind`). The model is choosing a length
# for a FORM as much as for a brief, and a story that has to land a turn is not paced
# like an account of a place.
WHAT = {
    "drama": (
        "a narrated vertical drama: ONE continuous first-person story, cut into beats, "
        "which has to open, turn and pay off"
    ),
    "fandom": (
        "a narrated vertical video set inside a world the operator wrote down, told "
        "from inside it as fact — an account, a record or an argument about that world"
    ),
}

SYSTEM = (
    "You are deciding HOW LONG a narrated vertical video should be, before anyone "
    "writes it. You are given the brief it will be written from, and nothing else.\n"
    "Answer with the length the MATERIAL deserves. Not a round number, not a format "
    "convention, not the longest you could justify.\n"
    "How to judge it:\n"
    "  • The brief below takes about {brief_s:.0f} seconds to say out loud at this "
    "video's speaking rate. If it IS the piece — a finished text written to be spoken, "
    "a list of things to say, an account already in its own words — then that number is "
    "your answer, adjusted only for what telling it aloud adds or drops.\n"
    "  • If the brief is a story or an argument to be built, give it the time its turns "
    "need and not a second more. The shape has to land; padding is what makes a short "
    "video feel long.\n"
    "  • If the brief only NAMES a subject — or there is none — choose what that subject "
    "is worth, as someone who knows the format would.\n"
    "A vertical video is watched to the end or not at all. Answer between {lo:.0f} and "
    "{hi:.0f} seconds; most pieces belong far nearer the floor than the ceiling.\n"
    "{clip_rule}"
    'Respond with JSON only: {{"seconds": <number>, "why": "<one short sentence in '
    'English: what in the brief decided it>"}}.'
)

CLIP_RULE = (
    "The video is cut into shots of about {clip_s:.0f} seconds each, so a length that "
    "is a whole number of them wastes nothing — answer a multiple of {clip_s:.0f} where "
    "the material does not argue otherwise.\n"
)


def suggest_length(
    llm, *, brief: str, brief_s: float, kind: str, lang: str, clip_s: float = 0.0
) -> tuple[float, str]:
    """How many seconds this brief is worth, and the model's one-line reason.

    `brief_s` is how long the brief itself takes to say (`pipeline.drama.speech_seconds`)
    and is computed by the caller: this module knows about prompts, not about how fast
    a particular run's voice speaks.

    Never raises and never returns zero: a failed or nonsensical answer falls back to
    `DEFAULT_FREE_S`, because every caller is about to cut a shot list out of whatever
    comes back and there is no sane way to cut one out of nothing."""
    brief = brief.strip()
    system = SYSTEM.format(
        brief_s=brief_s, lo=MIN_FREE_S, hi=MAX_FREE_S,
        clip_rule=CLIP_RULE.format(clip_s=clip_s) if clip_s > 0 else "",
    )
    user = (
        f"WHAT IS BEING WRITTEN: {WHAT.get(kind, 'a narrated vertical video')}.\n"
        f"It is narrated in {lang}.\n\n"
        f"THE BRIEF ({len(brief)} characters):\n{brief or '(none — nothing was asked for)'}"
    )
    try:
        data = llm.complete_json("length", system, user)
        seconds = float(data.get("seconds") or 0.0)
        why = str(data.get("why") or "").strip()
    except Exception as e:  # noqa: BLE001 — a run without a length is not a crash
        log.warning("could not ask for a length (%s); using %.0fs", e, DEFAULT_FREE_S)
        return DEFAULT_FREE_S, "the length call failed"
    if seconds <= 0:
        log.warning("the length answer was unusable; using %.0fs", DEFAULT_FREE_S)
        return DEFAULT_FREE_S, "the length answer was unusable"
    return min(max(seconds, MIN_FREE_S), MAX_FREE_S), why
