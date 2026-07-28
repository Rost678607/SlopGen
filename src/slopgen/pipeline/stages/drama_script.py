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
narration for a beat is sized to the seconds of the clip that will carry it. Clip
length is authored too, and it changes what a beat IS: a short clip is one framing,
while a long one is written as a sequence of several scenes (see `shot_rule`).

A native ad, when enabled, is woven into the plot at the scenario level — a
natural in-story lead-in that culminates in one spoken ad beat — rather than a
bolted-on interruption.
"""

from __future__ import annotations

from ...llm.characters import recompile_if_dirty
from ..context import AppContext
from ..drama import clip_bounds, plan_slots, word_budget
from ..job import Scene, VideoJob
from ..parts import normalize_scene_parts, requested_parts
from .idea import LANG_NAMES
from .script import _count_profanity, _inject_profanity, profanity_rule

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
    '  • "part": the output part number for this shot (1 if there is only one part);\n'
    '  • "narration": the spoken text for this shot, in {lang} — roughly {words} words for every '
    "10 seconds of its clip_s, so a longer beat carries proportionally more speech;\n"
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
    '  • "clip_s": how many seconds THIS beat runs — see the length rule above.\n'
    "FIRST BEAT — COLD OPEN HOOK: drop the viewer into the most dramatic or surprising moment of the "
    "story (1-2 punchy sentences; tease, don't resolve). Its video_prompt must be visually arresting — "
    "dynamic framing, high contrast, peak-tension action. After this beat, the story unfolds from the "
    "beginning and builds toward that moment.\n"
    "Give the drama a clear arc (hook → rise → turn → payoff) across about {beats} beats and roughly "
    "{duration:.0f} seconds total (you MAY use a few more or fewer beats — up to ~{tol:.0f}s over/under — "
    "when the story flows better). The cast sheet is AUTHORITATIVE for every character's gender, age "
    "and looks — never contradict it, in the narration or in video_prompt, pronouns included (a girl "
    "on the sheet is never 'he'). Two characters in one shot must stay visually distinct.\n"
    "{part_rule}"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": [{{"part": 1, "narration": "...", '
    '"video_prompt": "...", "characters": ["..."], "clip_s": 8, "is_ad": false}}, ...]}}.'
)

# One beat is one generated clip, and the WRITER sizes it: a fixed length for every
# beat is what made long clips monotonous — the same one scene holding three actions,
# over and over — while a drawn-out moment and a sharp exchange want opposite lengths.
# What a beat must NEVER be is a list of cuts: told "wide shot THEN close-up THEN
# reaction", real generators (Grok, Kling, …) read that as a storyboard and open the
# clip with every shot on screen at once, as a split-screen grid.
SHOT_RULE = (
    "Each beat is ONE clip: a single unbroken take, one camera, one continuous action. "
    'YOU choose how long each beat runs and return it as "clip_s" — anywhere from {lo:.0f} '
    "to {hi:.0f} seconds, averaging about {avg:.0f}. Size it to the moment: let a drawn-out "
    "one (a slow approach, a long look, a confession) run long, and cut a fast exchange or a "
    "sharp turn into SEVERAL short beats rather than one lazy long take. Vary the lengths — "
    "beats that all run the same read as monotonous.\n"
    "Whatever the length, a video_prompt describes ONE continuous action in one or two "
    "sentences. Never a list of moments, never 'THEN', no cuts, montage, sequence, split "
    "screen, collage, grid or storyboard: a generator renders those literally and puts every "
    "shot on screen at the same time. A long beat is not several shots — it is one shot with "
    "more room: the camera moves, the action develops, there is no edit."
)


def shot_rule(average_s: float) -> str:
    lo, hi = clip_bounds(average_s)
    return SHOT_RULE.format(lo=lo, hi=hi, avg=average_s)


DRAMA_PROFANITY = (
    "\nIn this first-person voice, swearing is the MC's genuine reaction in that exact moment — "
    "baked into the sentence and coloured by the specific feeling (rage, shock, glee, contempt). "
    "It must never be the same generic interjection ('Пиздец.', 'Заебись.', 'Сука.') dropped in as a "
    "standalone beat after every plot turn; that reads as filler, not a voice."
)

VISUAL_NOTES_RULE = (
    "\nVISUAL CONSTRAINTS — they bind every video_prompt and NOTHING else. The story, the "
    "narration and the characters' actions are written as if the constraint did not exist; "
    "only what the shot SHOWS obeys it (a gunfight is still a gunfight, it is merely shown "
    "with the props the constraint allows). Constraints: {notes}"
)

AD_RULES = (
    "\nNATIVE AD: weave a natural, in-story lead-in toward the sponsor and place EXACTLY ONE beat with "
    '"is_ad": true at roughly 60-70% of the story. In that beat the narrator (same voice and mood) '
    "organically brings up the product and says the link is in the description, based on these talking "
    "points: {points}. The lead-in beats before it should make the mention feel earned, not abrupt. "
    'Give the ad beat a normal "video_prompt" and "characters" too.'
)

PART_RULES = (
    "\nMULTI-PART OUTPUT: split the story into exactly {parts} ordered parts. "
    'Every scene must have integer "part" from 1 to {parts}; part numbers never go backwards. '
    "The final beat of every non-final part MUST be the strongest unresolved moment available: "
    "a revelation, betrayal, discovery, threat, impossible choice, or emotional reversal that cuts at peak tension. "
    "Do not resolve that moment inside the same part. The next part starts with the immediate fallout, "
    "not a recap. Make each part feel publishable on its own while still demanding the next part.\n"
)


def _roster(cast) -> str:
    """The cast sheet handed to the writer. It also shows what each name expands into
    in a shot prompt, so the writer can see that naming a character is enough — the
    footage stage substitutes the full look for the name (see SYSTEM)."""
    if not cast:
        return "(no fixed cast — invent characters as the story needs)"
    lines = []
    for c in cast:
        look = c.appearance.strip() or "(improvise looks)"
        age = f", age {c.age}" if c.age else ""
        lines.append(f"- {c.name}{age}: {look}")
        tag = ", ".join(t.strip() for t in c.visual_prompt.split(",")[:4] if t.strip())
        if tag:
            lines.append(f"    (slopgen expands the name into: {tag}…)")
    return "\n".join(lines)


def _clip_s(scene: dict) -> float:
    """The beat's own length, as the writer chose it (0 = fall back to the slot's)."""
    try:
        return max(float(scene.get("clip_s") or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_scenes(data: dict) -> list[Scene]:
    out: list[Scene] = []
    for s in data.get("scenes", []):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or s.get("text") or "").strip()
        if not narration:
            continue
        try:
            part = int(s.get("part", 1) or 1)
        except (TypeError, ValueError):
            part = 1
        out.append(Scene(
            part=part,
            text=narration,
            video_prompt=str(s.get("video_prompt", "")).strip(),
            characters=[str(c).strip() for c in s.get("characters", []) if str(c).strip()],
            clip_target_s=_clip_s(s),
            is_ad=bool(s.get("is_ad")),
        ))
    return out


def _assign_slots(scenes: list[Scene], slots) -> None:
    """Pin each non-ad scene to a generator slot (in order; cycled if the writer
    produced more beats than planned). Ad scenes use the ad's own clips, so they
    take no generator slot — footage sets their length from the chosen ad clip."""
    if not slots:
        return
    i = 0
    for scene in scenes:
        if scene.is_ad:
            scene.clip_target_s = scene.clip_target_s or slots[0].clip_seconds
            continue
        slot = slots[i % len(slots)]
        scene.gen_model = slot.model
        scene.key_mode = slot.key_mode
        scene.key = slot.key
        # the writer sized this beat; the slot only says who generates it. Clamp to
        # the band so one runaway number cannot swallow the whole budget.
        lo, hi = clip_bounds(slot.clip_seconds)
        scene.clip_target_s = (
            min(max(scene.clip_target_s, lo), hi) if scene.clip_target_s else slot.clip_seconds
        )
        i += 1


def run(job: VideoJob, ctx: AppContext) -> None:
    p = ctx.params
    lang = LANG_NAMES.get(p.lang, p.lang)
    # compile the cast to generation-ready visual prompts (lazy; in-memory only)
    cast = [recompile_if_dirty(ctx.llm, c) for c in ctx.cast]
    # hand the compiled per-character prompts to footage (so it needn't recompile)
    job.cast_prompts = {c.name: c.visual_prompt for c in cast if c.visual_prompt}

    slots = plan_slots(ctx.orchestration, p.duration_s, p.clip_seconds)
    beats = len(slots)
    avg_clip_s = sum(s.clip_seconds for s in slots) / beats
    avg_words = word_budget(avg_clip_s, p.lang)
    parts = requested_parts(p)

    system = SYSTEM.format(
        lang=lang, words=avg_words, beats=beats,
        duration=p.duration_s, tol=p.duration_tol_s,
        shot_rule=shot_rule(avg_clip_s),
        part_rule=PART_RULES.format(parts=parts) if parts > 1 else "",
    )
    system += profanity_rule(p.profanity, p.lang)
    if p.profanity > 0:
        system += DRAMA_PROFANITY
    if p.visual_notes.strip():
        system += VISUAL_NOTES_RULE.format(notes=p.visual_notes.strip())
    if ctx.native_ad_on:
        system += AD_RULES.format(points=ctx.ad.native.talking_points)

    scenario = p.scenario.strip() or "(invent a compelling premise that fits the cast)"
    user = (
        f"Premise / plot:\n{scenario}\n\nCast:\n{_roster(cast)}\n\n"
        f"Write the narration in {lang}; keep every video_prompt in English."
    )
    data = ctx.llm.complete_json("drama_script", system, user)

    scenes = _parse_scenes(data)
    if not scenes:
        raise ValueError("LLM returned an empty drama script")

    # guarantee the requested swearing level (same focused rewrite as info mode)
    if p.profanity > 0:
        expected = -(-len(scenes) * p.profanity // 100)
        if _count_profanity(scenes, p.lang) < expected:
            rewritten = _inject_profanity([s.text for s in scenes], p.profanity, p.lang, ctx.llm)
            if rewritten:
                for s, t in zip(scenes, rewritten):
                    s.text = t

    # keep at most one ad beat even if the model over-delivers
    seen_ad = False
    for s in scenes:
        if s.is_ad and seen_ad:
            s.is_ad = False
        seen_ad = seen_ad or s.is_ad

    normalize_scene_parts(scenes, parts)
    if parts > 1:
        missing = set(range(1, parts + 1)) - {s.part for s in scenes}
        if missing:
            raise ValueError(
                f"drama script has {len(scenes)} scenes and cannot fill "
                f"{parts} non-empty parts"
            )
    _assign_slots(scenes, slots)
    job.scenes = scenes
    job.topic = str(data.get("title", "")).strip() or (p.scenario.strip()[:80] or "AI drama")
