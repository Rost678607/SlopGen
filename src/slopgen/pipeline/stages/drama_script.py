"""Drama stage 1: write the narrated web-drama script.

Given a premise (scenario) and a cast, the main character narrates their own story
in a single first-person voice — living the events, voicing inner thoughts, and
dropping other characters' lines in raw and inline with no "said X" attribution.
The story is broken into BEATS — one beat per
generated clip — and each beat carries two texts: the spoken ``narration`` (in the
content language) and an English ``video_prompt`` for the AI image/video model,
plus the list of cast ``characters`` visible in the shot (so footage can inject
their compiled visual prompts).

Beat count and each beat's length come from the orchestration plan (see
pipeline/drama.py): the timeline is authored in minutes ± a tolerance, and the
narration for a beat is sized to the seconds of the clip that will carry it — at the
speech rate the run voices it with, since a faster voice fits more story into the same
shot (see `..drama.word_budget`). Clip
length is authored too, and it changes what a beat IS: a short clip is one framing,
while a long one is written as a sequence of several scenes (see `shot_rule`).

Everything about HOW a script is planned, windowed and cut into episodes lives in
`beats.py`, which the fandom mode shares; this module is only the drama's half of
that contract — who narrates, and the prompts that say so. See `beats` for the
rationale behind the outline pass and the windows.

A native ad, when enabled, is woven into the plot at the scenario level — a
natural in-story lead-in that culminates in one spoken ad beat — rather than a
bolted-on interruption.
"""

from __future__ import annotations

from ..context import AppContext
from ..job import VideoJob
from .beats import (
    PREMISE_RULE,
    Window,
    shot_rule,
    write_beats,
)

SYSTEM = (
    "You are the writer of a narrated, anime-style vertical web drama (короткая дорама). "
    "The MAIN CHARACTER narrates their OWN story in first person, in {lang} — a single voice, "
    "as if retelling what happened to them. Everything is filtered through the MC's 'я'. "
    "That one voice does three things, blended freely within a beat: "
    "(1) events as the MC lives them, first person "
    "('Но он толкнул меня. Все ахнули. Я налился яростью.'); "
    "(2) the MC's raw inner thoughts, same voice, no quote marks needed "
    "('Ну всё, ты труп.'); "
    "(3) other characters' spoken lines dropped in RAW and inline — no 'сказал он', no "
    "'усмехнулась она', no attribution before OR after; the listener tells who is speaking from "
    "context and tone alone ('Долго ты будешь прогуливать? — Простите, я плохо сплю. — Останешься "
    "после уроков.'). "
    "NEVER narrate the MC in third person ('Юки был лузером' ❌ → 'Я был лузером' ✅). "
    "NEVER tag a line with who said it. One unbroken first-person voice — never a screenplay. "
    "Break the story into BEATS. {shot_rule} For each beat give:\n"
    '  • "narration": the spoken text for this shot, in {lang} (~{words} words), advancing the plot;\n'
    '  • "video_prompt": an ENGLISH text-to-image/video prompt describing THIS shot — the setting, '
    "which characters are on screen and what they are doing, camera framing and mood. Token-dense, "
    "concrete, comma-friendly; do NOT translate the narration, describe the VISUAL. "
    "Refer to each person present BY NAME, spelled exactly as the cast sheet has it — slopgen "
    "swaps every name for that character's full visual description before the prompt reaches the "
    "generator, which is what keeps two characters in one shot from being blended. Do not describe "
    "their looks yourself. Everything else in it must be English.\n"
    "  ONE CONTINUOUS SHOT, described in one or two sentences: a single camera, a single unbroken "
    "action. Never a list of moments — a generator handed several beats renders them all at once, "
    "as a split-screen grid, before playing anything.\n"
    '  • "characters": the list of cast names visible in this shot (subset of the cast; [] if none).\n'
    "{open_rule}"
    "{arc_rule} The cast sheet is AUTHORITATIVE for every character's gender, age "
    "and looks — never contradict it, in the narration or in video_prompt, pronouns included (a girl "
    "on the sheet is never 'he'). Two characters in one shot must stay visually distinct.\n"
    "{part_rule}"
    "{premise_rule}"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": [{{"narration": "...", '
    '"video_prompt": "...", "characters": ["..."], "is_ad": false}}, ...]}}.'
)


# --------------------------------------------------------------------------
# The outline pass: cut the brief up once, before a single beat is written.
# --------------------------------------------------------------------------

OUTLINE_SYSTEM = (
    "You are the STORY EDITOR of a narrated vertical web drama. You do not write the drama — you "
    "cut the operator's brief into exactly {wins} consecutive STRETCHES, which {wins} different "
    "writers then write. Each writer sees only its own stretch, this outline, and the last few "
    "lines written before it, so whatever you leave out of the outline never reaches the page.\n"
    "Read the WHOLE brief and plan the WHOLE story before you write stretch 1. The stretches are "
    "slices of ONE story, in order, and together they must use the brief up completely: every "
    "event, character, place, object, number and spoken line in it belongs to exactly ONE "
    "stretch. Nothing may be dropped, nothing may be told twice, nothing may be moved out of the "
    "order the brief puts it in. Give each stretch its fair share of the material — a stretch is "
    "the same length as every other, so do not pack half the brief into the first two and leave "
    "the rest to stretch a single scene across a dozen beats.\n"
    "If the brief is short and leaves the story to be invented, invent it — and the same rule "
    "then holds: each stretch owns its own material and no other stretch's.\n"
    "For each stretch give:\n"
    '  • "covers": what happens in it, in order — 2-5 concrete sentences about events, not '
    "themes or mood. Written in {lang}.\n"
    '  • "details": the concrete things from the brief this stretch is responsible for — names, '
    "numbers, objects, places, revelations, lines that are actually spoken. Short items, in the "
    "brief's own wording. This is the checklist its writer must spend; put each detail in the one "
    "stretch it belongs to and nowhere else.\n"
    '  • "ends_on": ONE sentence — where the story stands when this stretch ends. The next '
    "stretch begins from exactly there.\n"
    "The last stretch ends the story, unless the brief directs the writer to end it otherwise, in "
    "which case plan for that instead.\n"
    "{part_rule}"
    "The rule below is addressed to the writers, and it binds you first: an instruction the "
    "operator wrote TO them is never material to plan a stretch around.\n"
    "{premise_rule}"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "stretches": [{{"covers": "...", '
    '"details": ["...", "..."], "ends_on": "..."}}, ...]{part_json}}}.'
)


class DramaWriter:
    """The drama's half of the beat contract (see `beats.Writer`)."""

    kind = "drama"
    fallback_title = "AI drama"
    self_timed = False  # clip length is the operator's (see beats._assign_slots)

    def empty_brief(self, ctx: AppContext) -> str:
        return "(invent a compelling premise that fits the cast)"

    def outline_system(self, ctx, *, wins, lang, part_rule, part_json):
        return OUTLINE_SYSTEM.format(
            wins=wins, lang=lang, part_rule=part_rule, part_json=part_json,
            premise_rule=PREMISE_RULE,
        )

    def outline_user(self, ctx, *, brief, roster, beats, windows):
        return (
            "The operator's brief — story material and, where they address you directly, "
            f"instructions to follow rather than to write down.\n{brief}\n\n"
            f"Cast:\n{roster}\n\n"
            f"The drama runs {beats} beats, cut into {len(windows)} stretches of "
            f"{', '.join(str(b - a) for a, b in windows)} beats."
        )

    def window_system(self, ctx, w: Window, *, lang, roster=""):
        # a drama's cast sheet stays in the user turn, next to the premise it belongs
        # to: it is small, it is the operator's own, and there is nothing to cache
        return SYSTEM.format(
            lang=lang, words=w.words, shot_rule=shot_rule(w.clip_s),
            open_rule=w.open_rule, arc_rule=w.arc, part_rule=w.part_rule,
            premise_rule=PREMISE_RULE,
        )

    def window_user(self, ctx, w: Window, *, brief, roster, tail, lang):
        user = (
            "Premise / plot — the operator's brief to you: story material and, where they address "
            f"you directly, instructions to follow rather than to write down.\n{brief}\n\n"
            f"Cast:\n{roster}\n\n"
        )
        if tail:
            user += f"The beats already written end like this:\n{tail}\n\n"
        return user + f"Write the narration in {lang}; keep every video_prompt in English."

    def tools(self, ctx) -> dict | None:
        return None


def run(job: VideoJob, ctx: AppContext) -> None:
    write_beats(job, ctx, DramaWriter())
