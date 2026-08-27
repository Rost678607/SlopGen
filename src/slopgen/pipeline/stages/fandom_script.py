"""Fandom stage 1: write the narrated script SET INSIDE a world the operator wrote.

Structurally this is the drama: a run of beats, planned by an outline pass and written
a window at a time, each beat carrying spoken `narration` and an English
`video_prompt` (see `beats.py`, which holds all of that machinery). What is different
is the one thing the mode exists for — the narrator's relationship to the world.

The failure this mode is built against is the model's default posture toward anything
fictional: it explains. Handed a world document, a model narrates ABOUT it — "in this
universe", "the author never clarifies", "fans have long theorised", "unlike our
world" — which is a video about a document, not a video from a place. So the contract
starts by taking that posture away (`WORLD_RULE`): the world is simply real, the lore
is its documented record, and there is no outside to compare it to. Gaps in the record
are gaps in what is KNOWN, not gaps in what was written down — which is also what
makes lore theories work, since a theory is only interesting if the thing it is about
is real.

Two narrators are on offer, chosen per run (`params.fandom_voice`):

  * `resident` — someone who lives there, first person, the world as daily life. The
    drama's voice, pointed at a world instead of a plot.
  * `chronicler` — an archivist, researcher or crank OF that world, reading its
    records as real documents and building theories out of them. A video essay whose
    author happens to live inside its subject.

The world reaches the writer in three layers, cheapest first, because none of them
works alone (see `llm/lore.py` for the full reasoning):

  1. the compiled canon sheet, in every window — an inventory, so the writer knows a
     thing exists even when it would never have thought to ask about it;
  2. the outline pass, which reads the whole lore once and hands each window the
     concrete facts that window is responsible for spending (`beats.DETAILS_RULE`);
  3. `lore_lookup`, the archivist tool, for the detail the writer knows it is missing
     — the only layer that costs a full reading of the documents per question.

The world's cast is not a fourth layer, and the mode is careful to say so (see
`CAST_RULE`). It is a WARDROBE: a list of what things look like, where one entry may be
one character, a body of identical faceless ones, or a whole kind of them. Everything a
character IS reaches the writer through the three layers above, like every other fact
about the world — which is the point, since a world's people are made of the same
material as its weather and its ledgers, and splitting them off into little character
sheets is how a video ends up about four people standing in a place.
"""

from __future__ import annotations

from ...llm.tools import LORE_LOOKUP_TOOL, make_lore_lookup
from ..context import AppContext
from ..job import VideoJob
from .beats import (
    MAX_BEAT_S,
    MIN_BEAT_S,
    PREMISE_RULE,
    Window,
    write_beats,
)

# A drama's beats are all the same length because the operator bought that length from
# a generator whose free daily tier they are rationing — so the writer is told the
# number and writes to it. Nobody is rationing anything here, and the material argues
# the other way: a held photograph of a wax seal wants three seconds, a mule train
# coming up a path wants ten, and forcing both to five makes one a flicker and the
# other a stare. So the writer times each beat, and sizes its own narration to fit.
SHOT_RULE = (
    "YOU CHOOSE HOW LONG EACH BEAT IS. Give every beat a \"seconds\" between "
    "{lo:.0f} and {hi:.0f}, and let the material decide: a held detail, a face, a "
    "document, a single revealed fact wants a short beat; a movement, a process, an "
    "arrival, something the eye should watch happen wants a long one. Vary them — a "
    "run of identically-timed shots is what makes a video feel like a slideshow of "
    "nothing. The whole piece should come to about {total:.0f} seconds across roughly "
    "{beats} beats, so if you spend a long beat somewhere, spend short ones nearby.\n"
    "The narration of a beat must FIT the seconds you gave it: about {wps:.1f} spoken "
    "words per second at this video's speaking rate. A beat of {lo:.0f}s therefore "
    "carries a phrase, not a paragraph.\n"
    "{shape}"
)

# What one beat IS depends on what it is made of, and the difference matters to the
# model writing the shot description: a generator handed "then" renders every moment at
# once, while a still cannot contain a "then" at all.
SHAPE_VIDEO = (
    "Each beat is ONE clip: a single unbroken take, one camera, one continuous action. "
    "A video_prompt describes that action in one or two sentences. Never a list of "
    "moments, never 'THEN', no cuts, montage, sequence, split screen, collage, grid or "
    "storyboard: a generator renders those literally and puts every shot on screen at "
    "the same time. A long beat is not several shots — it is one shot with more room: "
    "the camera moves, the action develops, there is no edit."
)
SHAPE_PHOTO = (
    "Each beat is ONE STILL PICTURE, held on screen and slowly panned across while the "
    "narration plays. So a video_prompt describes a PHOTOGRAPH, not an action: what is "
    "in the frame, from where, in what light. Nothing moves and nothing happens in it — "
    "no 'walking', no 'turning', no 'as she reaches for'. Where the narration tells of "
    "something happening, the picture is the moment of it that a photographer would "
    "have caught."
)


def shot_rule(clip_s: float, *, total: float, beats: int, wps: float, photo: bool) -> str:
    return SHOT_RULE.format(
        lo=MIN_BEAT_S, hi=MAX_BEAT_S, total=total, beats=beats, wps=wps,
        shape=SHAPE_PHOTO if photo else SHAPE_VIDEO,
    )


# The whole mode, in one rule. Every clause of it is a posture a model falls into on
# its own when handed a world document, and each one breaks the illusion in a
# different way: naming the medium, naming the author, addressing an audience of
# fans, or reaching for our world as the reference frame.
WORLD_RULE = (
    "THE WORLD IS REAL. It is not a story, a setting, a franchise, a game, an anime, a "
    "book, a universe or anyone's invention, and you are not describing one. It is "
    "simply the world — the one you are in. The documents you were given are its "
    "records: chronicles, notes, hearsay, things people there know.\n"
    "Therefore you NEVER say, imply or hint at any of the following, in narration, in "
    "the title, or in a video_prompt: 'fandom', 'canon', 'lore', 'the author', 'the "
    "creator', 'the series', 'the story', 'fictional', 'imaginary', 'fans', 'viewers', "
    "'the audience', 'this world' as something held at arm's length, or any comparison "
    "to the real world, to Earth, to our history or to our technology. There is no "
    "outside to compare this to.\n"
    "Where the records are silent, the world is not: something IS true there, it is "
    "merely not known, disputed, forgotten or deliberately unrecorded. Say that. Never "
    "say a thing 'was not specified', 'is never explained' or 'we are not told' — that "
    "is talking about a document, and it is the one thing that gives the game away. "
    "The impersonal passive is the same slip wearing a coat ('why, is not explained', "
    "'the reason is not given'): it still points at a text with a gap in it. Name who "
    "does not know and why, in the world — 'nobody at the station could say', 'the "
    "ledger for that winter is missing the page', 'she never told anyone'. If you "
    "cannot name them, the thing is simply unknown, and you say so as a fact about the "
    "world rather than about a record of it.\n"
    "Everything you invent to fill a gap must be the kind of thing this world already "
    "contains: no object, word, institution or turn of phrase that its records give you "
    "no reason to believe exists.\n"
)

# The canon sheet is an inventory, not prose, and a model handed an inventory tends to
# recite it. What it is FOR is knowing the world well enough to speak casually about
# it — the way someone who lives somewhere mentions a street without explaining it.
CANON_RULE = (
    "\nWHAT YOU KNOW ABOUT THE WORLD — its records, compiled. This is authoritative: "
    "every name, number, date and rule in it is fact, and nothing you write may "
    "contradict it.\n{canon}\n"
    "This is a reference, not a script. Do not recite it, do not define its terms for "
    "anyone, do not deliver it as exposition. You know these things the way you know "
    "your own street: you use them in passing and only explain one when the moment "
    "genuinely turns on it.\n"
)

# When the lore is short enough that compiling it would cost more than it saves, the
# writer simply gets it (see fandom_canon.SMALL_LORE_CHARS).
LORE_RULE = (
    "\nTHE RECORDS OF THIS WORLD. Authoritative — every name, number, date and rule in "
    "them is fact, and nothing you write may contradict them.\n{lore}\n"
    "Use them the way someone who lives there would: in passing, never recited, never "
    "explained to an outsider.\n"
)

LORE_TOOL_RULE = (
    "\nTHE ARCHIVIST: you may call `lore_lookup` to ask the keeper of the records any "
    "question about this world, as many times as you need. Ask BEFORE you commit to a "
    "specific name, date, number, custom, place or rule that your compiled knowledge "
    "above does not already give you — the records hold far more detail than the "
    "summary does, in their own exact wording. Never call it to have something invented "
    "for you: if the archivist says a thing is not recorded, then it is not known in "
    "this world, and you write accordingly.\n"
)

# What the brief asks for is usually one of two things, and they want opposite
# handling: "tell me about X" is reportage, "what really happened to X" is an argument.
# A model given a theory brief without this drifts into summarising the evidence and
# never commits to a conclusion.
BRIEF_RULE = (
    "\nWHAT THIS VIDEO IS ABOUT — the operator's brief. It may ask you to recount "
    "something about the world, or to argue a THEORY about it. If it asks for a theory, "
    "genuinely build one: lay out the evidence from the records, name what does not add "
    "up, and commit to a conclusion. It is a claim made INSIDE the world by someone who "
    "lives there — never a fan theory, never a reading of a text, never a guess about "
    "what an author meant. Say 'the ledgers disagree', never 'the lore is inconsistent'.\n"
)

# What the cast sheet is, and — more importantly — what it is NOT. A world's sheet is a
# wardrobe, not a cast list: it holds looks and nothing else, because everything a
# character IS is written in the records instead (see `config.models.CharacterConfig`).
# A writer handed a description of a coat and told it is a character will happily supply
# the rest — a name's meaning, a grievance, a dead brother — and that invention is
# indistinguishable, in the finished video, from something the world actually contains.
# The other half of the rule is number: an entry may stand for four hundred identical
# figures, and left unsaid it becomes one man with a name.
CAST_RULE = (
    "The cast sheet is AUTHORITATIVE for what each character LOOKS like and for what "
    "sort of thing it is: a person or not, one of them or many, and its gender where it "
    "has one. Never contradict it, in the narration or in a video_prompt — pronouns and "
    "NUMBER included. An entry marked as a GROUP or a KIND is not one individual and has "
    "no personal story: it is however many of them a shot needs, and the narration "
    "speaks of them the way it would of any anonymous many.\n"
    "The sheet says NOTHING ELSE about anyone. Who a character is, what they have done, "
    "what they are like and what anyone thinks of them is in the records of this world, "
    "and there alone — take it from there, and never invent it out of a description of a "
    "coat. A character the records do not speak of is simply someone the records do not "
    "speak of.\n"
    "Two characters in one shot must stay visually distinct.\n"
)

SYSTEM_RESIDENT = (
    "You are writing a narrated vertical video, in {lang}, spoken by ONE person who "
    "LIVES in the world described below. They speak in first person about their own "
    "world — what they have seen, what they were told, what everyone there knows and "
    "what nobody there can explain. This is their life, not a subject they are "
    "introducing.\n"
    "That one voice does three things, blended freely within a beat: "
    "(1) what they have lived or witnessed, first person; "
    "(2) their own take on it, plain and unguarded; "
    "(3) other people's words dropped in RAW and inline — no 'said the postmistress', "
    "no attribution before OR after; the listener tells who is speaking from context "
    "and tone alone.\n"
    "NEVER lecture, NEVER introduce the world as a topic, NEVER open by naming and "
    "defining it. One unbroken first-person voice — never a screenplay, never a "
    "narrator over a documentary.\n"
    "{world_rule}"
    "Break it into BEATS. {shot_rule} For each beat give:\n"
    '  • "seconds": how long this beat is on screen (you choose — see the rule above);\n'
    '  • "narration": the spoken text for this shot, in {lang}, sized to those seconds '
    "(a {words}-word beat is about what a five-second one holds), carrying the piece "
    "forward;\n"
    "{video_prompt_rule}"
    '  • "characters": the list of named characters from the cast sheet visible in this '
    "shot (subset of the cast; [] if none).\n"
    "{open_rule}"
    "{arc_rule} {cast_rule}"
    "{part_rule}"
    "{premise_rule}"
    "{world_block}"
    "{brief_rule}"
    "\nTHE OUTPUT CONTRACT, which nothing above overrides:\n"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": '
    '[{{"seconds": <number>, "narration": "...", "video_prompt": "...", '
    '"characters": ["..."], "is_ad": false}}, ...]}}.'
)

SYSTEM_CHRONICLER = (
    "You are writing a narrated vertical video, in {lang}, spoken by someone who "
    "STUDIES the world described below and lives in it — an archivist, a chronicler, a "
    "researcher, a crank who has read too many ledgers. They speak about their own "
    "world's records the way a historian speaks about theirs: with sources, with "
    "dates, with the parts that do not add up.\n"
    "The voice is dry, specific and quietly obsessive. It cites: what one record says, "
    "what another says instead, who claimed what and when. It may use 'I' for its own "
    "reasoning ('I counted them twice') but it is not the hero of anything. It quotes "
    "other people's words RAW and inline, no attribution tags.\n"
    "NEVER present the world as a subject for outsiders, NEVER open with a definition, "
    "NEVER address an audience that might be unfamiliar with it. Everyone listening "
    "lives here too — what they lack is not the basics, it is what you found in the "
    "records.\n"
    "{world_rule}"
    "Break it into BEATS. {shot_rule} For each beat give:\n"
    '  • "seconds": how long this beat is on screen (you choose — see the rule above);\n'
    '  • "narration": the spoken text for this shot, in {lang}, sized to those seconds '
    "(a {words}-word beat is about what a five-second one holds), advancing the account "
    "or the argument;\n"
    "{video_prompt_rule}"
    '  • "characters": the list of named characters from the cast sheet visible in this '
    "shot (subset of the cast; [] if none).\n"
    "{open_rule}"
    "{arc_rule} {cast_rule}"
    "{part_rule}"
    "{premise_rule}"
    "{world_block}"
    "{brief_rule}"
    "\nTHE OUTPUT CONTRACT, which nothing above overrides:\n"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "scenes": '
    '[{{"seconds": <number>, "narration": "...", "video_prompt": "...", '
    '"characters": ["..."], "is_ad": false}}, ...]}}.'
)

# Same contract as the drama's, plus the one thing a world adds: a generator knows
# nothing about it, so a shot has to be described in ordinary visual language even
# when the narration calls it by the world's own name.
VIDEO_PROMPT_RULE = (
    '  • "video_prompt": an ENGLISH text-to-image/video prompt describing THIS shot — '
    "the setting, which of the world's characters are on screen and what they are "
    "doing, camera framing and mood. Token-dense, concrete, comma-friendly; do NOT "
    "translate the narration, describe the VISUAL. Refer to each character present BY "
    "NAME, spelled exactly as the cast sheet has it — slopgen swaps every name for that "
    "character's full visual description before the prompt reaches the generator, which "
    "is what keeps two of them in one shot from being blended. Do not describe their "
    "looks yourself. A character need not be a person: it may be a creature, a machine, "
    "a vehicle or a structure, and the sheet says which. Nor need it be ONE: an entry "
    "marked as a group or a kind stands for all of them at once, and a shot showing "
    "several says so around the name — 'three <name> hauling a crate uphill' — never by "
    "pluralising or altering the name itself. "
    "The image generator has never heard of this world: never put one of its own terms "
    "in a video_prompt untranslated — describe what the thing LOOKS like in plain "
    "English (not 'the winter carry', but 'figures in heavy coats carrying mail sacks "
    "single file along a snowbound mountain path'). Everything else in it must be "
    "English.\n"
    "  ONE CONTINUOUS SHOT, described in one or two sentences: a single camera, a "
    "single unbroken action. Never a list of moments — a generator handed several "
    "beats renders them all at once, as a split-screen grid, before playing anything.\n"
)

# The opening is where the "explaining a world" reflex is strongest: told to hook, a
# model writes an establishing line that introduces the setting to a newcomer.
OPEN_RULE_FANDOM = (
    "FIRST BEAT — START INSIDE, NOT AT AN INTRODUCTION: open on a concrete moment, "
    "object or claim, already in the middle of it (1-2 punchy sentences). Never open by "
    "naming, introducing or situating the world, never 'let me tell you about', never a "
    "sentence that would only be written for someone who has never been here. The hook "
    "is the specific thing itself. Its video_prompt must be visually arresting — "
    "dynamic framing, high contrast.\n"
)

OUTLINE_SYSTEM = (
    "You are the STORY EDITOR of a narrated vertical video set in the world whose "
    "records are given below. You do not write it — you cut the operator's brief into "
    "exactly {wins} consecutive STRETCHES, which {wins} different writers then write. "
    "Each writer sees only its own stretch, this outline, a compiled summary of the "
    "world and the last few lines written before it. Whatever you leave out of the "
    "outline never reaches the page — and you are the ONLY pass that reads the full "
    "records, so the concrete detail you do not hand out is the detail the video loses.\n"
    "{world_rule}"
    "Read the WHOLE brief and the WHOLE records, then plan the whole piece before you "
    "write stretch 1. The stretches are slices of ONE piece, in order. Give each "
    "stretch its fair share of the material — a stretch is the same length as every "
    "other, so do not pack half the brief into the first two.\n"
    "For each stretch give:\n"
    '  • "covers": what it deals with, in order — 2-5 concrete sentences about events, '
    "claims and moments, not themes or mood. Written in {lang}.\n"
    '  • "details": the concrete things from the RECORDS this stretch is responsible '
    "for spending — names, dates, numbers, places, objects, customs, quoted lines, in "
    "the records' own exact wording. This is the checklist its writer must spend, and "
    "the main way the world's real texture reaches the page: be generous and be "
    "specific. Put each detail in the one stretch it belongs to and nowhere else.\n"
    '  • "ends_on": ONE sentence — where the piece stands when this stretch ends. The '
    "next stretch begins from exactly there.\n"
    "The last stretch ends the piece, unless the brief directs otherwise, in which case "
    "plan for that instead.\n"
    "{part_rule}"
    "The rule below is addressed to the writers, and it binds you first: an instruction "
    "the operator wrote TO them is never material to plan a stretch around.\n"
    "{premise_rule}"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "stretches": '
    '[{{"covers": "...", "details": ["...", "..."], "ends_on": "..."}}, ...]{part_json}}}.'
)


class FandomWriter:
    """The fandom's half of the beat contract (see `beats.Writer`)."""

    kind = "fandom"
    fallback_title = "Хроника"
    self_timed = True  # the writer sizes every shot (see SHOT_RULE below)

    def __init__(self, canon: str, lore: str, lore_tool: bool, photo: bool = False):
        self.canon = canon
        self.lore = lore
        # the tool earns its cost only when the records hold more than the sheet does
        self.lore_tool = lore_tool and bool(canon)
        # a slideshow is written differently from a run of clips: a still cannot hold
        # an action, so the shot descriptions have to be photographs (see SHAPE_PHOTO)
        self.photo = photo

    # -- what the writer is told about the world ---------------------------

    def _world_block(self) -> str:
        """The world itself: the compiled sheet, or the records whole when they are
        short enough that compiling them would save nothing."""
        if self.canon:
            block = CANON_RULE.format(canon=self.canon)
        else:
            block = LORE_RULE.format(lore=self.lore)
        return block + (LORE_TOOL_RULE if self.lore_tool else "")

    def empty_brief(self, ctx: AppContext) -> str:
        return (
            "(no brief — choose something from this world worth an account of its own: "
            "a custom, a place, an unexplained event, a person, or a question its "
            "records leave open)"
        )

    # -- the outline pass --------------------------------------------------

    def outline_system(self, ctx, *, wins, lang, part_rule, part_json):
        return OUTLINE_SYSTEM.format(
            wins=wins, lang=lang, part_rule=part_rule, part_json=part_json,
            world_rule=WORLD_RULE, premise_rule=PREMISE_RULE,
        )

    def outline_user(self, ctx, *, brief, roster, beats, windows):
        return (
            "THE RECORDS OF THIS WORLD — read all of it before planning anything.\n"
            f"{self.lore}\n\n"
            "THE BRIEF — what this video is about: material and, where the operator "
            "addresses you directly, instructions to follow rather than to write "
            f"down.\n{brief}\n\n"
            "Characters who may appear — what they LOOK like, and nothing more. Not all "
            "of them are people, and an entry may be one figure, a body of identical "
            f"ones, or a whole kind:\n{roster}\n\n"
            f"The piece runs {beats} beats, cut into {len(windows)} stretches of "
            f"{', '.join(str(b - a) for a, b in windows)} beats."
        )

    # -- one window --------------------------------------------------------

    def window_system(self, ctx, w: Window, *, lang):
        template = (
            SYSTEM_CHRONICLER
            if ctx.params.fandom_voice == "chronicler"
            else SYSTEM_RESIDENT
        )
        tone = (ctx.fandom.tone if ctx.fandom else "").strip()
        return template.format(
            lang=lang, words=w.words,
            shot_rule=shot_rule(
                w.clip_s, total=ctx.params.duration_s, beats=w.beats_total,
                wps=w.words / max(w.clip_s, 0.1), photo=self.photo,
            ),
            video_prompt_rule=VIDEO_PROMPT_RULE,
            world_rule=WORLD_RULE,
            open_rule=OPEN_RULE_FANDOM if w.index == 0 else "",
            arc_rule=w.arc, cast_rule=CAST_RULE,
            part_rule=w.part_rule, premise_rule=PREMISE_RULE,
            world_block=self._world_block()
            + (f"\nHOW THIS ONE IS TOLD — the operator's note on register: {tone}\n"
               if tone else ""),
            brief_rule=BRIEF_RULE,
        )

    def window_user(self, ctx, w: Window, *, brief, roster, tail, lang):
        user = (
            "THE BRIEF — what this video is about: material and, where the operator "
            "addresses you directly, instructions to follow rather than to write "
            f"down.\n{brief}\n\n"
            "Characters who may appear — what they LOOK like, and nothing more. Not all "
            "of them are people, and an entry may be one figure, a body of identical "
            f"ones, or a whole kind:\n{roster}\n\n"
        )
        if tail:
            user += f"The beats already written end like this:\n{tail}\n\n"
        return user + f"Write the narration in {lang}; keep every video_prompt in English."

    # -- the archivist -----------------------------------------------------

    def tools(self, ctx) -> dict | None:
        if not self.lore_tool:
            return None
        return {"lore_lookup": (LORE_LOOKUP_TOOL, make_lore_lookup(ctx.llm, self.lore))}


def run(job: VideoJob, ctx: AppContext) -> None:
    fandom = ctx.fandom
    write_beats(job, ctx, FandomWriter(
        canon=job.canon,
        lore=ctx.lore,
        lore_tool=bool(fandom and fandom.lore_tool),
        photo=ctx.params.medium == "photo",
    ))
