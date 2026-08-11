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

The script is written a WINDOW of beats at a time rather than all at once. Asked for
a whole feature-length script in one response, a model spends its attention
front-loaded — the opening beats track the premise sentence by sentence and the rest
turns to summary, dropping named props, sub-plots and reversals the operator wrote
down. Each window instead gets a stated slice of the premise to cover, the tail of
what came before, and enough room to spend on it (see `ARC_WINDOW`).

The premise is a BRIEF rather than source text: next to the plot the operator writes
directions to the writer ("break it off with no ending", "don't explain the letter"),
which are to be followed and never voiced (see `PREMISE_RULE`).

A native ad, when enabled, is woven into the plot at the scenario level — a
natural in-story lead-in that culminates in one spoken ad beat — rather than a
bolted-on interruption.
"""

from __future__ import annotations

from ...llm.characters import recompile_if_dirty
from ..context import AppContext
from ..drama import plan_slots, plan_windows, word_budget
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
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": [{{"part": 1, "narration": "...", '
    '"video_prompt": "...", "characters": ["..."], "is_ad": false}}, ...]}}.'
)

# One beat is one generated clip, and the WRITER sizes it: a fixed length for every
# beat is what made long clips monotonous — the same one scene holding three actions,
# over and over — while a drawn-out moment and a sharp exchange want opposite lengths.
# What a beat must NEVER be is a list of cuts: told "wide shot THEN close-up THEN
# reaction", real generators (Grok, Kling, …) read that as a storyboard and open the
# clip with every shot on screen at once, as a split-screen grid.
SHOT_RULE = (
    "Each beat is ONE clip of EXACTLY {avg:.0f} seconds: a single unbroken take, one camera, "
    "one continuous action. Every beat is that same length — you do not choose it. What you "
    "choose is how much story goes into each one, and that is where the rhythm comes from: "
    "let a drawn-out moment (a slow approach, a long look, a confession) run across SEVERAL "
    "consecutive beats rather than cramming it into one, and compress a fast exchange or a "
    "sharp turn into a single beat. Never pad a beat with three unrelated actions just to "
    "fill {avg:.0f} seconds — that is what reads as monotonous.\n"
    "Whatever the length, a video_prompt describes ONE continuous action in one or two "
    "sentences. Never a list of moments, never 'THEN', no cuts, montage, sequence, split "
    "screen, collage, grid or storyboard: a generator renders those literally and puts every "
    "shot on screen at the same time. A long beat is not several shots — it is one shot with "
    "more room: the camera moves, the action develops, there is no edit."
)


def shot_rule(clip_s: float) -> str:
    return SHOT_RULE.format(avg=clip_s)


# The opening belongs to the video, so only the window that owns beat 0 is told
# about it — every window given the rule would open with its own first beat.
# It used to ask for a flash-forward to the peak, which is a spoiler: the viewer was
# handed the turn before the story earned it, and the beats in between played out as
# a foregone conclusion. The story now runs in order, and the hook is the opening
# situation itself.
OPEN_RULE = (
    "FIRST BEAT — START AT THE BEGINNING: open where the story actually begins and let it run "
    "forward in order. Never flash forward to a later moment, never tease, hint at or show a "
    "fragment of what is coming — the viewer must learn nothing the narrator does not know yet. "
    "The hook is the opening situation itself: begin at the moment the ordinary breaks, already "
    "inside the action (1-2 punchy sentences; no scene-setting preamble, no 'it all started when'). "
    "Its video_prompt must still be visually arresting — dynamic framing, high contrast.\n"
)

ARC_WHOLE = (
    "Give the drama a clear arc (hook → rise → turn → payoff) across about {beats} beats and roughly "
    "{duration:.0f} seconds total (you MAY use a few more or fewer beats — up to ~{tol:.0f}s over/under — "
    "when the story flows better)."
)

# A window is a slice of one arc, not an arc of its own. Left to itself a window
# will pace the whole premise into the beats it was given — racing to the ending in
# the first window and then having nothing left — so each one is told where in the
# premise it starts and where it must have got to by its last beat.
ARC_WINDOW = (
    "You are writing beats {first}-{last} of {beats_total} — window {win} of {wins} of ONE continuous "
    "drama, not a story of its own. Write EXACTLY {beats} beats. Your window opens about {from_pct:.0f}% "
    "of the way through the premise and its last beat must land at about {to_pct:.0f}% — pace it so the "
    "story arrives there, neither racing ahead to material a later window needs nor stalling on material "
    "an earlier one already told. {tail_rule}{end_rule}"
)

# The premise field is where the operator talks TO the writer — "оборви резко, без
# концовки", "не объясняй письмо", "он старше, чем ты написал". Handed that as plain
# plot text, the model either voiced it back (the narrator announcing that the story
# ends abruptly) or treated it as an event to dramatise. So the premise is declared
# for what it is: a brief, part story and part direction, and direction wins over the
# defaults below it without ever reaching the page.
PREMISE_RULE = (
    "\nTHE PREMISE IS A BRIEF WRITTEN TO YOU, THE WRITER — never source text to be voiced. It "
    "mixes story material (what happens, to whom, where) with direct instructions about how to "
    "write it: how to end it, what to keep or leave out, how to pace it, which detail to correct. "
    "Those instructions are often addressed to you in the second person ('cut it off abruptly with "
    "no ending', \"don't explain the letter\", 'make him colder than you did'). Obey them, then "
    "write only the story they ask for. NEVER voice, quote, paraphrase, answer or acknowledge "
    "them: no narration, no title and no video_prompt may carry a word the operator was saying to "
    "YOU rather than about the story, and the narrator never remarks on how the story is being "
    "told. An instruction about the story's shape, opening, ending, tone or content OVERRIDES the "
    "arc, opening and part rules above; it never overrides the output format, the language, or "
    "one continuous shot per beat.\n"
)

CONTINUE_RULE = (
    "Your first beat continues straight on from the last beat already written (shown below) — "
    "no recap, no re-introduction, no repeating what it said. "
)
FINAL_RULE = (
    "This is the LAST window: your final beat is the story's payoff and must resolve it — unless "
    "the premise asks you to end otherwise (to break off unresolved, to stop mid-scene), in which "
    "case do exactly that and never say that you are doing it. "
)


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
    '"is_ad": true among the beats you are writing now — this is the point of the story the ad belongs '
    "to, so do not defer it. In that beat the narrator (same voice and mood) "
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
        # the operator's clip length is authoritative — the writer only writes to it
        scene.clip_target_s = slot.clip_seconds
        i += 1


def _tail(scenes: list[Scene], count: int = 3) -> str:
    """The last few narrations already written, verbatim — what the next window
    continues from. Only the spoken text: a window needs to know where the story
    stands, not how the previous shots were framed."""
    return "\n".join(f"  … {s.text}" for s in scenes[-count:])


def run(job: VideoJob, ctx: AppContext) -> None:
    p = ctx.params
    lang = LANG_NAMES.get(p.lang, p.lang)
    # compile the cast to generation-ready visual prompts (lazy; in-memory only)
    cast = [recompile_if_dirty(ctx.llm, c) for c in ctx.cast]
    # hand the compiled per-character prompts to footage (so it needn't recompile)
    job.cast_prompts = {c.name: c.visual_prompt for c in cast if c.visual_prompt}

    slots = plan_slots(ctx.orchestration, p.duration_s, p.clip_seconds)
    beats = len(slots)
    parts = requested_parts(p)
    scenario = p.scenario.strip() or "(invent a compelling premise that fits the cast)"
    roster = _roster(cast)

    # The script is written a window at a time (see drama.plan_windows). The ad beat
    # sits at ~65% of the story, so only the window holding that position is told
    # about it — every window given the rule would place one of its own.
    windows = plan_windows(beats)
    ad_window = next(
        (i for i, (a, b) in enumerate(windows) if a <= int(beats * 0.65) < b), len(windows) - 1
    )

    scenes: list[Scene] = []
    title = ""
    for wi, (first, last) in enumerate(windows):
        win_beats = last - first
        # clip length can differ per window under a hybrid orchestration, so the
        # narration budget is taken from the slots this window actually writes to
        win_clip_s = sum(s.clip_seconds for s in slots[first:last]) / win_beats
        if len(windows) == 1:
            arc = ARC_WHOLE.format(beats=beats, duration=p.duration_s, tol=p.duration_tol_s)
        else:
            arc = ARC_WINDOW.format(
                first=first + 1, last=last, beats_total=beats, beats=win_beats,
                win=wi + 1, wins=len(windows),
                from_pct=100.0 * first / beats, to_pct=100.0 * last / beats,
                tail_rule=CONTINUE_RULE if wi else "",
                end_rule=FINAL_RULE if wi == len(windows) - 1 else "",
            )
        system = SYSTEM.format(
            lang=lang, words=word_budget(win_clip_s, p.lang, p.tts_rate),
            shot_rule=shot_rule(win_clip_s),
            open_rule=OPEN_RULE if wi == 0 else "",
            arc_rule=arc,
            part_rule=PART_RULES.format(parts=parts) if parts > 1 else "",
            premise_rule=PREMISE_RULE,
        )
        system += profanity_rule(p.profanity, p.lang)
        if p.profanity > 0:
            system += DRAMA_PROFANITY
        if p.visual_notes.strip():
            system += VISUAL_NOTES_RULE.format(notes=p.visual_notes.strip())
        if ctx.native_ad_on and wi == ad_window:
            system += AD_RULES.format(points=ctx.ad.native.talking_points)

        user = (
            "Premise / plot — the operator's brief to you: story material and, where they address "
            f"you directly, instructions to follow rather than to write down.\n{scenario}\n\n"
            f"Cast:\n{roster}\n\n"
        )
        if scenes:
            user += f"The beats already written end like this:\n{_tail(scenes)}\n\n"
        user += f"Write the narration in {lang}; keep every video_prompt in English."

        data = ctx.llm.complete_json("drama_script", system, user)
        got = _parse_scenes(data)
        if not got:
            raise ValueError(
                f"LLM returned no beats for window {wi + 1}/{len(windows)} of the drama script"
            )
        scenes.extend(got)
        title = title or str(data.get("title", "")).strip()
        ctx.progress("script", wi + 1, len(windows))

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
    # the title comes from the first window — it is the one that saw the story open
    job.topic = title or (p.scenario.strip()[:80] or "AI drama")
