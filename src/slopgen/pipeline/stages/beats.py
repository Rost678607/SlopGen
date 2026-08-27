"""The beat machinery both narrated modes are built on (drama, fandom).

A beat is one generated clip carrying one piece of narration, and a script is a run
of them. Everything about turning a brief into that run is the same whoever is
speaking: the story is planned once by an OUTLINE pass that reads the whole brief,
then written a WINDOW of beats at a time against that plan, then cut into episodes at
the beats the plan chose. What differs between the modes is only WHO narrates and
WHAT they are narrating about — the contract handed to the writer. That is what a
:class:`Writer` supplies; this module supplies the rest.

The script is written a WINDOW of beats at a time rather than all at once. Asked for
a whole feature-length script in one response, a model spends its attention
front-loaded — the opening beats track the premise sentence by sentence and the rest
turns to summary, dropping named props, sub-plots and reversals the operator wrote
down.

Windows alone were not enough, and this is where a long story used to fall apart
after the middle. A window told only that its beats sit "about 55% of the way through
the premise" has to eyeball which sentences of a two-thousand-word brief that is, and
it eyeballs badly: the middle windows re-tell what an earlier one already covered,
skip the props and sub-plots in between, and by the back half the story is running on
whatever the model remembers rather than on the brief. So the premise is cut up FIRST,
by an OUTLINE pass that reads all of it at once: per window it says what happens in
that stretch, which concrete details of the brief that stretch is responsible for
spending, and where the story stands when it ends. Each window then writes its own
stretch against that plan, with the whole outline in front of it so it can see where
it is (see `ARC_PLAN`). The percentage windows are still there as the fallback for
when the outline call comes back unusable (see `ARC_WINDOW`).

Episode boundaries are planned in the same pass, as beat numbers, and each window is
told which of ITS beats close an episode — so the beat before a cut is written as a
cliffhanger and the beat after it as the fallout (see `_part_rule`). They used to be
asked of every window at once: each of eighteen windows was told to "split the story
into exactly 10 parts", which is how a story ended up with a cliffhanger every few
beats and an arc nowhere. Which episode a beat ends up in is decided by the plan; the
writer is not asked to label its beats at all, only to write the right one at a cut.

The premise is a BRIEF rather than source text: next to the plot the operator writes
directions to the writer ("break it off with no ending", "don't explain the letter"),
which are to be followed and never voiced (see `PREMISE_RULE`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...config.loader import CONFIGS_DIR, cache_visual_prompt
from ...llm.characters import recompile_if_dirty
from ..context import AppContext
from ..drama import plan_slots, plan_windows, word_budget
from ..job import Scene, VideoJob
from ..parts import normalize_scene_parts, requested_parts
from .idea import LANG_NAMES
from .script import _count_profanity, _inject_profanity, profanity_rule

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

# What a window gets INSTEAD of the percentage above once the outline pass has run:
# not "you are somewhere around the middle" but the actual events of this stretch, the
# actual details of the brief it owns, and the actual state it has to leave the story
# in. The whole outline goes in too (see PLAN_MAP) — a writer that cannot see the
# stretches on either side of it has no way to tell what is already told from what is
# still to come, which is exactly how the back half of a long drama used to drift.
ARC_PLAN = (
    "You are writing beats {first}-{last} of {beats_total} — stretch {win} of {wins} of ONE "
    "continuous drama, not a story of its own. Write EXACTLY {beats} beats.\n"
    "YOUR STRETCH, as the story editor planned it. This, and nothing outside it, is what you "
    "dramatise:\n{covers}\n"
    "{details}"
    "By your last beat the story must stand exactly here: {ends_on}\n"
    "Never tell material another stretch owns — not the ones behind you (already written) and "
    "not the ones ahead (they are waiting for it). {tail_rule}{end_rule}"
)

# Cheap, and it is what keeps eighteen separate calls writing the same story: one line
# per stretch, so every writer sees the shape of the whole drama and where in it its
# own beats sit.
PLAN_MAP = (
    "\nTHE WHOLE DRAMA, one line per stretch — where the story stands at the end of each:\n"
    "{lines}\n"
)

DETAILS_RULE = (
    "These concrete details from the brief belong to YOUR stretch and to no other. Every one of "
    "them must appear in your beats — spend them all:\n{items}\n"
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

# Episode boundaries reach the writer as BEAT NUMBERS of its own window, never as
# "split the story into 10 parts". Handed the latter, each of eighteen windows dutifully
# split ITS beats into ten parts, so the drama came out with a cliffhanger every few
# beats, no arc, and part labels so scrambled that the pipeline threw them away and cut
# the episodes evenly anyway.
# The writer is not asked to LABEL anything either, only to write the right beat at the
# cut. Asked for a part number per beat, it reads the field as "beat number" and counts
# 1, 2, 3 … through a window that is wholly inside one episode; the labels the pipeline
# actually keeps come from the plan (see `_label_parts`).
PART_RULES = (
    "\nEPISODE CUTS: this drama is published as {parts} separate episodes cut out of the same "
    "continuous run of beats. The story is never restarted, recapped or summarised at a cut, and "
    "you never label, number or announce an episode — where the cuts fall is already decided, and "
    "your job is only to write the beats they fall between. {spans}\n{cliffs}"
)

PART_CLIFF = (
    "Your beat {beat} is the LAST beat of episode {part}: end it on the strongest unresolved "
    "moment your material holds — a revelation, a betrayal, a threat, an impossible choice, an "
    "emotional reversal — cut at peak tension and do NOT resolve it. Your beat {next_beat} opens "
    "episode {next_part} on the immediate fallout, mid-situation, with no recap of any kind.\n"
)

OUTLINE_PARTS = (
    "\nEPISODE CUTS: the drama is published as {parts} episodes cut out of its {beats} beats — "
    "beat 1 opens episode 1, and each cut opens the next one. Choose where the {cuts} cut(s) "
    'fall: "part_breaks", the beat numbers that OPEN an episode, strictly increasing, between 2 '
    "and {beats}, and close to an even split (about {even} beats per episode). Put each cut on "
    "the beat straight AFTER the strongest unresolved moment available near that point — a "
    "revelation, a betrayal, a threat, an impossible choice — so the episode before it ends at "
    "peak tension and stays unresolved until the next one. Make sure the stretch a cut falls in "
    "really does have that moment in it.\n"
    'Beat numbers belong in "part_breaks" and NOWHERE else: never write one into "covers", '
    '"details" or "ends_on", and never mention episodes, cuts or the plan there. Those fields are '
    "read by a writer who is told about its own cut separately, in its own beat numbering — a "
    '"detail" reading \'beat 49\' is one it will dutifully try to put on screen.\n'
)

# --------------------------------------------------------------------------
# What a mode has to supply: the contract handed to its writer.
# --------------------------------------------------------------------------


@dataclass
class Window:
    """One window's slice of the run, with everything shared already computed.

    A mode's :class:`Writer` reads this to build its own system prompt; it never has
    to work out where in the story it is, how long the clips are or which of its beats
    close an episode."""

    index: int  # 0-based window number
    first: int  # 0-based planned beat this window opens on
    last: int  # 0-based planned beat AFTER the last one it writes
    beats: int  # how many beats this window writes
    beats_total: int  # beats in the whole script
    windows: int  # how many windows there are
    clip_s: float  # average seconds of the clips THIS window writes to
    words: int  # narration budget for one of its beats
    arc: str  # ready-made: where in the story this window sits (plan/percentage)
    part_rule: str  # ready-made: which of its beats close an episode
    open_rule: str  # ready-made: the opening rule, on the first window only
    is_ad: bool  # this window is the one that places the native ad beat


class Writer(Protocol):
    """A mode's half of the script stage: who is narrating, and about what."""

    kind: str  # LLM call label, e.g. "drama" -> "drama_script" / "drama_outline"
    fallback_title: str  # job title when neither the outline nor a window gave one

    def empty_brief(self, ctx: AppContext) -> str:
        """Stand-in for a brief the operator left blank."""

    def outline_system(self, ctx: AppContext, *, wins: int, lang: str, part_rule: str,
                       part_json: str) -> str:
        """System prompt for the one pass that reads the whole brief."""

    def outline_user(self, ctx: AppContext, *, brief: str, roster: str, beats: int,
                     windows: list[tuple[int, int]]) -> str:
        """User turn for that pass."""

    def window_system(self, ctx: AppContext, w: Window, *, lang: str) -> str:
        """System prompt for one window. The shared suffixes (profanity, visual
        constraints, the ad) are appended by :func:`write_beats` afterwards."""

    def window_user(self, ctx: AppContext, w: Window, *, brief: str, roster: str,
                    tail: str, lang: str) -> str:
        """User turn for one window."""

    def tools(self, ctx: AppContext) -> dict | None:
        """Per-run function tools to offer the writer, or None."""

    # Whether this mode's writer sizes its own beats (see _assign_slots).
    self_timed: bool


@dataclass
class Stretch:
    """One window's slice of the story, as the outline pass planned it."""

    covers: str
    details: list[str] = field(default_factory=list)
    ends_on: str = ""


def _even_breaks(beats: int, parts: int) -> list[int]:
    """Beat numbers (1-based) that open each episode after the first, split evenly.

    The fallback when there is no outline to ask, and the sanity check on the one it
    gives back. Strictly increasing for any ``beats >= parts``."""
    return [1 + round(beats * k / parts) for k in range(1, max(parts, 1))]


def _clean_breaks(raw, beats: int, parts: int) -> list[int]:
    """Validate the outline's episode cuts, falling back to an even split.

    A cut outside the beat range, a repeated one, the wrong number of them, or a split
    so lopsided that one episode is a third of the length of another are all rejected
    wholesale: the point of letting the editor choose is a better cut than an even one,
    and a broken list is not that."""
    breaks = sorted({n for n in _ints(raw) if 2 <= n <= beats})
    if len(breaks) != max(parts - 1, 0):
        return _even_breaks(beats, parts)
    spans = [b - a for a, b in zip([1] + breaks, breaks + [beats + 1])]
    if min(spans) * 3 < beats / parts:
        return _even_breaks(beats, parts)
    return breaks


def _ints(raw) -> list[int]:
    out: list[int] = []
    for v in raw if isinstance(raw, list) else []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def _parse_stretches(data: dict, wins: int) -> list[Stretch]:
    """The outline's stretches, one per window — or nothing, which puts the stage back
    on the percentage windows. A short or long list is nothing: a stretch matched to
    the wrong window would send that window off to write the wrong part of the story,
    which is worse than no plan at all."""
    raw = data.get("stretches") or data.get("windows") or []
    if not isinstance(raw, list) or len(raw) != wins:
        return []
    out: list[Stretch] = []
    for item in raw:
        if not isinstance(item, dict):
            return []
        covers = str(item.get("covers") or item.get("summary") or "").strip()
        if not covers:
            return []
        details = item.get("details") or []
        out.append(Stretch(
            covers=covers,
            details=[str(d).strip() for d in details if str(d).strip()]
            if isinstance(details, list) else [],
            ends_on=str(item.get("ends_on") or "").strip(),
        ))
    return out


# --------------------------------------------------------------------------
# The outline pass: cut the brief up once, before a single beat is written.
# --------------------------------------------------------------------------


def outline(
    ctx: AppContext, writer: "Writer", *, brief: str, roster: str, lang: str,
    windows: list[tuple[int, int]], beats: int, parts: int,
) -> tuple[list[Stretch], list[int], str]:
    """Plan the whole script once: (stretches, episode-cut beats, title).

    One LLM call, and the only one that ever sees the entire brief against the entire
    beat budget. Returns no stretches when there is nothing to plan (a single window
    already sees everything) or when the answer came back unusable — the caller then
    writes the way it did before, a percentage of the premise at a time."""
    cuts = max(parts - 1, 0)
    if len(windows) < 2:
        return [], _even_breaks(beats, parts), ""
    system = writer.outline_system(
        ctx, wins=len(windows), lang=lang,
        part_rule=OUTLINE_PARTS.format(
            parts=parts, beats=beats, cuts=cuts, even=round(beats / parts)
        ) if cuts else "",
        part_json=', "part_breaks": [<beat numbers>]' if cuts else "",
    )
    user = writer.outline_user(
        ctx, brief=brief, roster=roster, beats=beats, windows=windows
    )
    data = ctx.llm.complete_json(
        f"{writer.kind}_outline", system, user, tools=writer.tools(ctx)
    )
    stretches = _parse_stretches(data, len(windows))
    breaks = _clean_breaks(data.get("part_breaks"), beats, parts) if cuts else []
    return stretches, breaks, str(data.get("title", "")).strip()

def _plan_map(stretches: list[Stretch], current: int) -> str:
    """The whole outline as one line per stretch, with the reader's own marked."""
    lines = "\n".join(
        f"  {i + 1}. {st.ends_on or st.covers.split('.')[0]}"
        + ("   ◀ YOURS" if i == current else "")
        for i, st in enumerate(stretches)
    )
    return PLAN_MAP.format(lines=lines)


def _details(stretch: Stretch) -> str:
    if not stretch.details:
        return ""
    return DETAILS_RULE.format(items="\n".join(f"  - {d}" for d in stretch.details))

# --------------------------------------------------------------------------
# Episode cuts
# --------------------------------------------------------------------------


def _part_rule(parts: int, breaks: list[int], first: int, last: int, beats: int) -> str:
    """What one window is told about episodes: which of ITS beats belong to which one,
    and which of them ends an episode.

    `first`/`last` are the window's half-open range of 0-based planned beats and
    `breaks` the 1-based beat numbers that open an episode; the beat numbers that come
    out are local to the window, because that is the only numbering its writer has.
    A window that owns no cut is simply told which episode its beats are, and a
    single-episode drama is told nothing at all."""
    if parts <= 1:
        return ""
    edges = [1] + list(breaks) + [beats + 1]
    spans: list[str] = []
    cliffs: list[str] = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]  # episode i+1 runs over global beats a … b-1
        lo, hi = max(a, first + 1), min(b - 1, last)  # its overlap with this window
        if lo > hi:
            continue
        spans.append(
            f"your beats {lo - first}-{hi - first} fall in episode {i + 1}"
            if hi > lo else f"your beat {lo - first} falls in episode {i + 1}"
        )
        if b - 1 == hi and i + 2 < len(edges):  # the cut itself falls inside this window
            cliffs.append(PART_CLIFF.format(
                beat=hi - first, part=i + 1, next_beat=hi - first + 1, next_part=i + 2,
            ))
    if not spans:
        return ""
    text = "; ".join(spans) + "."
    return PART_RULES.format(
        parts=parts, spans=text[0].upper() + text[1:], cliffs="".join(cliffs)
    )


def _cut_index(planned: int, windows: list[tuple[int, int]], counts: list[int]) -> int:
    """Where a PLANNED beat number lands in the scenes actually written.

    A window asked for thirteen beats does not always return thirteen, so a cut planned
    at beat 40 cannot simply be applied at scene 40. It is applied at the same offset
    inside the same window instead, which keeps every cut on the moment its writer was
    told to end an episode on."""
    beat = planned - 1  # 0-based
    base = 0
    for (a, b), n in zip(windows, counts):
        if beat < b:
            return base + min(max(beat - a, 0), n)
        base += n
    return base


def _label_parts(
    scenes: list[Scene], breaks: list[int], windows: list[tuple[int, int]], counts: list[int]
) -> None:
    """Stamp the episode number on every scene from the planned cuts.

    The writer is not asked for these labels and could not give good ones: it never saw
    more than its own stretch. The cuts, by contrast, are what its beats were WRITTEN to
    — the beat before one is a cliffhanger because the writer was told to make it one.
    The operator moves them at the ``cut`` breakpoint.

    Two planned cuts can land on the same scene when a window comes back much shorter
    than it was asked for. They are nudged apart rather than merged, because an episode
    with no scenes at all is what sends the whole cut back to an even split (see
    :func:`..parts.normalize_scene_parts`) — losing every good boundary over one."""
    total = len(scenes)
    edges: list[int] = []
    for k, planned in enumerate(breaks):
        lo = edges[-1] + 1 if edges else 1  # never empty the episode this cut closes
        hi = total - (len(breaks) - k)  # leave a scene for every cut still to come
        edges.append(min(max(_cut_index(planned, windows, counts), lo), max(hi, lo)))
    for i, scene in enumerate(scenes):
        scene.part = 1 + sum(1 for e in edges if e <= i)

# How a world's entry is introduced on the sheet. A drama's cast is a list of people
# and needs no such note; a world's is a list of LOOKS, and an entry may stand for one
# thing or for every one of its kind (see `config.models.Plurality`). Unmarked, a group
# reads as an individual and the writer gives it a name, a line of dialogue and a
# personal arc — four hundred wardens condensed into one man called Warden.
_PLURALITY_NOTE = {
    "one": "",
    "many": " [a GROUP: many of them, identical and faceless, all with this one look]",
    "class": " [a WHOLE KIND: every member of it looks broadly like this]",
}

# Only the fandom mode reaches this, and only because its sheet says nothing about who
# anyone IS — the world's records do (see stages/fandom_script). Told merely that a
# character exists, a writer invents the rest of it.
_WORLD_ROSTER_NOTE = (
    "\nThis sheet says ONLY what each of these looks like, which is all that is written "
    "down about them as characters. Who they are — their name's weight, their history, "
    "their rank, their temper, what they want — is in the records of the world, and "
    "nowhere else: read it there, and never invent it from a description of a coat. An "
    "entry marked as a GROUP or a KIND is not one person and has no personal story: "
    "name it in a shot and the shot holds as many of them as it needs."
)


# Compiling one character is a full round trip, and the calls are independent of each
# other, so they are made side by side. Six at once: enough to turn twenty-four minutes
# into four, not so many that a provider starts refusing.
log = logging.getLogger(__name__)

CAST_WORKERS = 6


def _character_file(ctx: AppContext, name: str) -> Path | None:
    """Where this character's file lives, or None if it has none.

    A world's people are shelved with the world; a drama's may be in the shared
    library, or nowhere at all when the operator built one for this run and never
    saved it. Only an existing file is ever written to (see `cache_visual_prompt`)."""
    candidates = []
    if ctx.is_fandom:
        world = ctx.store.fandoms.get(ctx.params.fandom)
        if world is not None and world.root:
            candidates.append(world.root / "characters" / f"{name}.toml")
    candidates.append(CONFIGS_DIR / "characters" / f"{name}.toml")
    return next((c for c in candidates if c.is_file()), None)


def _compile_cast(ctx: AppContext) -> list:
    """Turn the cast into generation-ready visual prompts, in parallel, and REMEMBER
    the result.

    This used to be a serial list comprehension whose docstring said "in-memory only",
    and it was the single slowest thing in the pipeline that nobody could see: a world
    of 44 characters spent about twenty-four minutes here, before the writer had been
    asked for a word, with no progress reported until the stage was over. Every run
    paid it again, because the compiled prompt was never written down.

    Three changes, all needed: the calls go out concurrently, each one reports, and
    each result is cached back into the character's own file so the second run costs
    nothing. `dirty` already meant "this cache is stale" — it just had nowhere to be
    stored."""
    cast = list(ctx.cast)
    todo = [c for c in cast if c.dirty]
    if not todo:
        return cast
    log.info("compiling %d/%d character prompts", len(todo), len(cast))
    done = 0
    ctx.progress("cast", 0, len(todo))
    out = {c.name: c for c in cast}
    with ThreadPoolExecutor(max_workers=min(CAST_WORKERS, len(todo))) as pool:
        futures = {
            pool.submit(recompile_if_dirty, ctx.llm, c, world=ctx.is_fandom): c
            for c in todo
        }
        for future in as_completed(futures):
            original = futures[future]
            try:
                compiled = future.result()
            except Exception as e:  # noqa: BLE001 — one bad character is not a run
                log.warning("could not compile '%s': %s", original.name, e)
                compiled = original
            out[compiled.name] = compiled
            path = _character_file(ctx, compiled.name)
            if path is not None and compiled.visual_prompt.strip():
                cache_visual_prompt(path, compiled.visual_prompt)
            done += 1
            ctx.progress("cast", done, len(todo))
    return [out[c.name] for c in cast]


def _roster(cast, world: bool = False) -> str:
    """The cast sheet handed to the writer. It also shows what each name expands into
    in a shot prompt, so the writer can see that naming a character is enough — the
    footage stage substitutes the full look for the name (see SYSTEM).

    `world` marks the fandom's sheet: no ages, a plurality note per entry, and a
    reminder that the sheet is a wardrobe rather than a cast list."""
    if not cast:
        return "(no fixed cast — invent characters as the story needs)"
    lines = []
    for c in cast:
        look = c.appearance.strip() or "(improvise looks)"
        if world:
            extra = _PLURALITY_NOTE.get(c.plurality, "")
        else:
            extra = f", age {c.age}" if c.age else ""
        lines.append(f"- {c.name}{extra}: {look}")
        tag = ", ".join(t.strip() for t in c.visual_prompt.split(",")[:4] if t.strip())
        if tag:
            lines.append(f"    (slopgen expands the name into: {tag}…)")
    return "\n".join(lines) + (_WORLD_ROSTER_NOTE if world else "")


# How far a self-sized beat may stray. A shot under two seconds is a flicker nobody
# reads; over twelve, a single held image or unbroken take stops being watchable. The
# clamp is here rather than in the prompt because a model asked for "3 to 10" will
# occasionally answer 45.
MIN_BEAT_S, MAX_BEAT_S = 2.0, 12.0


def _parse_scenes(data: dict) -> list[Scene]:
    out: list[Scene] = []
    for s in data.get("scenes", []):
        if not isinstance(s, dict):
            continue
        narration = str(s.get("narration") or s.get("text") or "").strip()
        if not narration:
            continue
        # `seconds` is only asked for by the modes that let the writer size its own
        # beats; elsewhere it is absent and the slot's length wins (see _assign_slots)
        try:
            secs = float(s.get("seconds") or 0.0)
        except (TypeError, ValueError):
            secs = 0.0
        # no `part` is read: which episode a beat belongs to is the plan's to decide
        # (see `_label_parts`), and the writer is not asked for it
        out.append(Scene(
            text=narration,
            video_prompt=str(s.get("video_prompt", "")).strip(),
            characters=[str(c).strip() for c in s.get("characters", []) if str(c).strip()],
            is_ad=bool(s.get("is_ad")),
            clip_target_s=min(max(secs, MIN_BEAT_S), MAX_BEAT_S) if secs else 0.0,
        ))
    return out


def _assign_slots(scenes: list[Scene], slots, keep_length: bool = False) -> None:
    """Pin each non-ad scene to a generator slot (in order; cycled if the writer
    produced more beats than planned). Ad scenes use the ad's own clips, so they
    take no generator slot — footage sets their length from the chosen ad clip.

    `keep_length` leaves a length the WRITER chose alone. In a drama the operator buys
    clip length from a generator whose free tier they are rationing, so it is theirs
    and the writer only writes to it; where nobody is rationing anything, the beat is
    as long as the beat needs to be."""
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
        if not (keep_length and scene.clip_target_s):
            scene.clip_target_s = slot.clip_seconds
        i += 1


def _tail(scenes: list[Scene], count: int = 3) -> str:
    """The last few narrations already written, verbatim — what the next window
    continues from. Only the spoken text: a window needs to know where the story
    stands, not how the previous shots were framed."""
    return "\n".join(f"  … {s.text}" for s in scenes[-count:])

# --------------------------------------------------------------------------
# Writing the run of beats.
# --------------------------------------------------------------------------


def write_beats(job: VideoJob, ctx: AppContext, writer: Writer) -> None:
    """Plan, write and cut the whole script into `job.scenes`.

    The stage body both narrated modes share. Every step here is mode-agnostic; the
    only mode-specific thing that happens is asking `writer` for the two prompts."""
    p = ctx.params
    lang = LANG_NAMES.get(p.lang, p.lang)
    # a world's characters need not be people — nor individuals (see
    # llm/characters._ANY_FORM and _PLURALITY)
    cast = _compile_cast(ctx)
    # hand the compiled per-character prompts to footage (so it needn't recompile)
    job.cast_prompts = {c.name: c.visual_prompt for c in cast if c.visual_prompt}

    slots = plan_slots(ctx.orchestration, p.duration_s, p.clip_seconds)
    beats = len(slots)
    parts = requested_parts(p)
    if parts > beats:
        raise ValueError(
            f"a {beats}-beat script cannot be cut into {parts} episodes — lengthen the "
            f"video, shorten the clips, or ask for fewer parts"
        )
    brief = p.scenario.strip() or writer.empty_brief(ctx)
    roster = _roster(cast, world=ctx.is_fandom)
    tools = writer.tools(ctx)

    # The script is written a window at a time (see drama.plan_windows). The ad beat
    # sits at ~65% of the story, so only the window holding that position is told
    # about it — every window given the rule would place one of its own.
    windows = plan_windows(beats)
    ad_window = next(
        (i for i, (a, b) in enumerate(windows) if a <= int(beats * 0.65) < b), len(windows) - 1
    )

    # plan the whole story (and where the episodes are cut) before writing any of it
    stretches, breaks, title = outline(
        ctx, writer, brief=brief, roster=roster, lang=lang,
        windows=windows, beats=beats, parts=parts,
    )
    if stretches:
        ctx.progress("outline", 1, 1)

    scenes: list[Scene] = []
    counts: list[int] = []  # beats each window actually returned, for the episode cuts
    for wi, (first, last) in enumerate(windows):
        win_beats = last - first
        # clip length can differ per window under a hybrid orchestration, so the
        # narration budget is taken from the slots this window actually writes to
        win_clip_s = sum(s.clip_seconds for s in slots[first:last]) / win_beats
        tail_rule = CONTINUE_RULE if wi else ""
        end_rule = FINAL_RULE if wi == len(windows) - 1 else ""
        if stretches:
            st = stretches[wi]
            arc = ARC_PLAN.format(
                first=first + 1, last=last, beats_total=beats, beats=win_beats,
                win=wi + 1, wins=len(windows), covers=st.covers, details=_details(st),
                ends_on=st.ends_on or "at the end of the stretch above",
                tail_rule=tail_rule, end_rule=end_rule,
            ) + _plan_map(stretches, wi)
        elif len(windows) == 1:
            arc = ARC_WHOLE.format(beats=beats, duration=p.duration_s, tol=p.duration_tol_s)
        else:
            arc = ARC_WINDOW.format(
                first=first + 1, last=last, beats_total=beats, beats=win_beats,
                win=wi + 1, wins=len(windows),
                from_pct=100.0 * first / beats, to_pct=100.0 * last / beats,
                tail_rule=tail_rule, end_rule=end_rule,
            )
        w = Window(
            index=wi, first=first, last=last, beats=win_beats, beats_total=beats,
            windows=len(windows), clip_s=win_clip_s,
            words=word_budget(win_clip_s, p.lang, p.tts_rate), arc=arc,
            part_rule=_part_rule(parts, breaks, first, last, beats),
            open_rule=OPEN_RULE if wi == 0 else "",
            is_ad=ctx.native_ad_on and wi == ad_window,
        )
        system = writer.window_system(ctx, w, lang=lang)
        system += profanity_rule(p.profanity, p.lang)
        if p.profanity > 0:
            system += DRAMA_PROFANITY
        if p.visual_notes.strip():
            system += VISUAL_NOTES_RULE.format(notes=p.visual_notes.strip())
        if w.is_ad:
            system += AD_RULES.format(points=ctx.ad.native.talking_points)

        user = writer.window_user(
            ctx, w, brief=brief, roster=roster,
            tail=_tail(scenes) if scenes else "", lang=lang,
        )
        data = ctx.llm.complete_json(f"{writer.kind}_script", system, user, tools=tools)
        got = _parse_scenes(data)
        if not got:
            raise ValueError(
                f"LLM returned no beats for window {wi + 1}/{len(windows)} of the script"
            )
        scenes.extend(got)
        counts.append(len(got))
        title = title or str(data.get("title", "")).strip()
        ctx.progress("script", wi + 1, len(windows))

    if not scenes:
        raise ValueError("LLM returned an empty script")

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

    if breaks:
        _label_parts(scenes, breaks, windows, counts)
    normalize_scene_parts(scenes, parts)
    if parts > 1:
        missing = set(range(1, parts + 1)) - {s.part for s in scenes}
        if missing:
            raise ValueError(
                f"the script has {len(scenes)} scenes and cannot fill "
                f"{parts} non-empty parts"
            )
    _assign_slots(scenes, slots, keep_length=getattr(writer, "self_timed", False))
    job.scenes = scenes
    # the title comes from the outline, which is the only pass that read the whole
    # story; failing that, from the first window — the one that saw it open
    job.topic = title or (p.scenario.strip()[:80] or writer.fallback_title)
