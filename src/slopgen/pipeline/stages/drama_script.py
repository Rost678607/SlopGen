"""Drama stage 1: write the narrated web-drama script.

Given a premise (scenario) and a cast, one offscreen narrator tells the story and
may quote characters' lines inline. The story is broken into BEATS — one beat per
generated shot — and each beat carries two texts: the spoken ``narration`` (in the
content language) and an English ``video_prompt`` for the AI image/video model,
plus the list of cast ``characters`` visible in the shot (so footage can inject
their compiled visual prompts).

Beat count and each beat's length come from the orchestration plan (see
pipeline/drama.py): the timeline is authored in minutes ± a tolerance, and the
narration for a beat is sized to the seconds of the clip that will carry it.

A native ad, when enabled, is woven into the plot at the scenario level — a
natural in-story lead-in that culminates in one spoken ad beat — rather than a
bolted-on interruption.
"""

from __future__ import annotations

from ...llm.characters import recompile_if_dirty
from ..context import AppContext
from ..drama import plan_slots, word_budget
from ..job import Scene, VideoJob
from .idea import LANG_NAMES
from .script import _count_profanity, _inject_profanity, profanity_rule

SYSTEM = (
    "You are the writer of a narrated, anime-style vertical web drama (короткая дорама). "
    "One offscreen NARRATOR tells the whole story in {lang}. "
    "The narrator has three tools: "
    "(1) plot narration ('Она вошла в зал…'); "
    "(2) the MC's inner thoughts / internal monologue, voiced in first person "
    "('«Они предали меня. Но я вернусь.»'); "
    "(3) direct quotes of any character, introduced briefly "
    "('Он усмехнулся: «Ты проиграла.»'). "
    "Mix all three freely within a beat for maximum emotional pull. "
    "There is still only ONE voice — never a screenplay with separate speaker lines. "
    "Break the story into BEATS. Each beat is exactly ONE short shot. For each beat give:\n"
    '  • "narration": the spoken text for this shot, in {lang} (~{words} words), advancing the plot;\n'
    '  • "video_prompt": an ENGLISH text-to-image/video prompt describing THIS shot — the setting, '
    "which characters are on screen and what they are doing, camera framing and mood. Token-dense, "
    "concrete, comma-friendly; do NOT translate the narration, describe the VISUAL.\n"
    '  • "characters": the list of cast names visible in this shot (subset of the cast; [] if none).\n'
    "FIRST BEAT — COLD OPEN HOOK: drop the viewer into the most dramatic or surprising moment of the "
    "story (1–2 punchy sentences; tease, don't resolve). Its video_prompt must be visually arresting — "
    "dynamic framing, high contrast, peak-tension action. After this beat, the story unfolds from the "
    "beginning and builds toward that moment.\n"
    "Give the drama a clear arc (hook → rise → turn → payoff) across about {beats} beats and roughly "
    "{duration:.0f} seconds total (you MAY use a few more or fewer beats — up to ~{tol:.0f}s over/under — "
    "when the story flows better). Keep characters consistent with the cast sheet.\n"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": [{{"narration": "...", '
    '"video_prompt": "...", "characters": ["..."], "is_ad": false}}, ...]}}.'
)

AD_RULES = (
    "\nNATIVE AD: weave a natural, in-story lead-in toward the sponsor and place EXACTLY ONE beat with "
    '"is_ad": true at roughly 60-70% of the story. In that beat the narrator (same voice and mood) '
    "organically brings up the product and says the link is in the description, based on these talking "
    "points: {points}. The lead-in beats before it should make the mention feel earned, not abrupt. "
    'Give the ad beat a normal "video_prompt" and "characters" too.'
)


def _roster(cast) -> str:
    if not cast:
        return "(no fixed cast — invent characters as the story needs)"
    lines = []
    for c in cast:
        look = c.appearance.strip() or "(improvise looks)"
        age = f", age {c.age}" if c.age else ""
        lines.append(f"- {c.name}{age}: {look}")
    return "\n".join(lines)


def _parse_scenes(data: dict) -> list[Scene]:
    out: list[Scene] = []
    for s in data.get("scenes", []):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or s.get("text") or "").strip()
        if not narration:
            continue
        out.append(Scene(
            text=narration,
            video_prompt=str(s.get("video_prompt", "")).strip(),
            characters=[str(c).strip() for c in s.get("characters", []) if str(c).strip()],
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
        scene.clip_target_s = slot.clip_seconds
        i += 1


def run(job: VideoJob, ctx: AppContext) -> None:
    p = ctx.params
    lang = LANG_NAMES.get(p.lang, p.lang)
    # compile the cast to generation-ready visual prompts (lazy; in-memory only)
    cast = [recompile_if_dirty(ctx.llm, c) for c in ctx.cast]
    # hand the compiled per-character prompts to footage (so it needn't recompile)
    job.cast_prompts = {c.name: c.visual_prompt for c in cast if c.visual_prompt}

    slots = plan_slots(ctx.orchestration, p.duration_s)
    beats = len(slots)
    avg_words = word_budget(sum(s.clip_seconds for s in slots) / beats, p.lang)

    system = SYSTEM.format(
        lang=lang, words=avg_words, beats=beats,
        duration=p.duration_s, tol=p.duration_tol_s,
    )
    system += profanity_rule(p.profanity, p.lang)
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

    _assign_slots(scenes, slots)
    job.scenes = scenes
    job.topic = str(data.get("title", "")).strip() or (p.scenario.strip()[:80] or "AI drama")
