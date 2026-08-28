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
    "nothing.\n"
    "THE LENGTH IS NOT NEGOTIABLE, and it is the one instruction here with arithmetic "
    "in it. What you are writing now runs EXACTLY {total:.0f} seconds across about "
    "{beats} beats: the \"seconds\" you assign must ADD UP TO {total:.0f}, and the "
    "narration you write must be about {chars} CHARACTERS in total — that is what "
    "{total:.0f} seconds of this voice says, at roughly {wps:.1f} words a second. Count "
    "it before you answer. Over budget is not a style choice: the shots are already "
    "paid for, so a piece written long is either cut or spoken fast, and both are worse "
    "than the piece you would have written to the number.\n"
    "So a beat of {lo:.0f}s carries a phrase and a beat of {hi:.0f}s carries a sentence "
    "or two — and if you spend a long beat somewhere, spend short ones nearby.\n"
    "Fitting the budget means saying FEWER THINGS, never saying things in fewer words. "
    "Whole sentences, with verbs in them, in the register the records describe: a line "
    "compressed into a telegraphic list of nouns ('three came, looked, said nothing, "
    "left') is not a shorter version of this voice, it is a different and worse one. "
    "When a beat will not fit, drop something from it — never the grammar.\n"
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


def shot_rule(clip_s: float, *, total: float, beats: int, chars: int, wps: float,
              photo: bool) -> str:
    """`total`/`beats`/`chars` are THIS WINDOW's share of the video, never the whole
    one. A window handed the whole number writes to it, and a piece cut into three
    windows comes out three times too long — which is most of how the first measured
    run reached 181 seconds against a budget of 120."""
    return SHOT_RULE.format(
        lo=MIN_BEAT_S, hi=MAX_BEAT_S, total=total, beats=beats, chars=chars, wps=wps,
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

# The brief is the one thing in this prompt the operator wrote themselves, and it kept
# losing to the world around it. Handed a finished text — six numbered rules, then the
# accounts of people who had met the things, then a sign-off — the writer treated it as
# a TOPIC: it kept the parts it liked, reordered them, and spent the first five of
# twenty beats on background out of the canon sheet that the brief does not contain at
# all. The rules it had actually been given started at beat six.
#
# That is what a brief becomes when it arrives labelled "what this video is about" next
# to seventeen thousand characters of world facts labelled authoritative — the sheet
# reads as the material and the brief as a suggestion about which corner of it to
# visit. So the two are declared for what they are, in that order: the brief IS the
# piece, and the records are a constraint on how it may be told.
#
# The clauses after that are each one observed failure. Invention: 'the Krivulya moves
# the bog about; that is why there are six rules' — a causal link neither the brief nor
# the records make, which in a finished video is indistinguishable from a fact of the
# world. The reverse: a beat describing plainly what a creature looks like, in a world
# whose records say nobody has ever got a good look at one. And the scaffolding: 'the
# instruction ends here', a line of the brief's furniture, read out loud in the voice.
BRIEF_RULE = (
    "\nTHE BRIEF — THIS IS THE VIDEO, not a topic for one. The operator wrote it, and "
    "it is the spine of what you write: its material, its order and its shape. "
    "Everything in it is in the piece; nothing that is not in it is added to the piece. "
    "If it lists six things, you say all six, in its order, and you do not open with a "
    "seventh. If it moves from one kind of material to another — rules, then accounts, "
    "then a close — you keep that structure and let the beats fall where it turns.\n"
    "You are not summarising it and you are not taking inspiration from it. You are "
    "SAYING it, in this world's voice, cut into beats and fitted to the time: the same "
    "content, reworded only as far as speaking it aloud requires. Where it runs shorter "
    "than the time you have, go DEEPER into what it already says — hold a moment, let a "
    "voice finish, let a detail land — never wider.\n"
    "It may instead only NAME a subject: a place, a custom, an unexplained event, a "
    "question. Then it is a topic rather than a text and you build the piece around it "
    "yourself. Judge by whether it carries content of its own — a sentence is a topic, "
    "a page is the piece. If it asks you to argue a THEORY, genuinely build one: lay "
    "out the evidence from the records, name what does not add up, and commit to a "
    "conclusion. It is a claim made INSIDE the world by someone who lives there — never "
    "a fan theory, never a reading of a text, never a guess about what an author meant. "
    "Say 'the ledgers disagree', never 'the lore is inconsistent'.\n"
    "THE RECORDS ABOVE ARE A CONSTRAINT, NOT MATERIAL. They say what this world "
    "contains, what its words are, and what nothing you write may contradict. They "
    "never add a subject the brief did not raise and never earn a beat of their own: "
    "you use them the way you use grammar — everywhere, and invisibly.\n"
    "INVENT NOTHING. Not a cause, not a reason, not a connection between two things "
    "that neither the brief nor the records make. If the brief says a rule is kept and "
    "does not say why, then why is not known, and you say THAT — not a reason you "
    "supplied. And where the records say a thing has never been seen clearly, it has "
    "not: neither the narration nor a video_prompt may show it plainly, and the shot is "
    "built around what people did see.\n"
    "The brief's own furniture is not narration. A heading, a numbering, a note about "
    "what the text is, a line marking where it stops ('that is the end of the "
    "instruction') — that is scaffolding you write TO, never text you read out. Nor is "
    "an instruction the operator addressed to you rather than to the world.\n"
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

# The cast sheet lives in the SYSTEM prompt rather than the user turn, and that is a
# cost decision as much as a prompt one: it is identical in every window, so putting it
# in front of everything that varies is what lets a provider's prompt cache serve it
# instead of re-reading it (see `window_system`).
ROSTER_RULE = (
    "\nCHARACTERS WHO MAY APPEAR — what each of them LOOKS like, and nothing more. Not "
    "all of them are people, and an entry may be one figure, a body of identical ones, "
    "or a whole kind:\n{roster}\n"
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
    "{world_block}"
    "{roster_rule}"
    "{cast_rule}"
    "{brief_rule}"
    "{premise_rule}"
    "\nBreak the piece into BEATS. For each beat give:\n"
    '  • "seconds": how long this beat is on screen (you choose — see the rule below);\n'
    '  • "narration": the spoken text for this shot, in {lang}, sized to those seconds, '
    "carrying the piece forward;\n"
    "{video_prompt_rule}"
    '  • "characters": the list of named characters from the cast sheet visible in this '
    "shot (subset of the cast; [] if none).\n"
    "{window_rule}"
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
    "{world_block}"
    "{roster_rule}"
    "{cast_rule}"
    "{brief_rule}"
    "{premise_rule}"
    "\nBreak the piece into BEATS. For each beat give:\n"
    '  • "seconds": how long this beat is on screen (you choose — see the rule below);\n'
    '  • "narration": the spoken text for this shot, in {lang}, sized to those seconds, '
    "advancing the account or the argument;\n"
    "{video_prompt_rule}"
    '  • "characters": the list of named characters from the cast sheet visible in this '
    "shot (subset of the cast; [] if none).\n"
    "{window_rule}"
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

# The planner is where the brief was lost first, and for a reason written into its own
# instructions: it used to be told that it alone reads the full records and that "the
# concrete detail you do not hand out is the detail the video loses", and its `details`
# field asked for things from the RECORDS. So it planned a tour of the world and hung
# the brief off it, which is exactly the shape the finished script came out in — five
# beats of lore, then the six rules the operator had actually written.
#
# It is now told the other way round: the brief is the thing being cut up, the records
# are what keeps the cutting honest, and a stretch's checklist is what the BRIEF put in
# it. The length arithmetic is here too, because the planner is the only pass that can
# distribute it — a stretch given half the brief and a sixth of the seconds is a
# window that cannot help but overrun.
OUTLINE_SYSTEM = (
    "You are the STORY EDITOR of a narrated vertical video set in the world whose "
    "records are given below. You do not write it — you cut the operator's brief into "
    "exactly {wins} consecutive STRETCHES, which {wins} different writers then write. "
    "Each writer sees only its own stretch, this outline, the brief, a compiled summary "
    "of the world and the last few lines written before it. Whatever you leave out of "
    "the outline never reaches the page.\n"
    "{world_rule}"
    "{brief_rule}"
    "Read the WHOLE brief, then the records, then plan the whole piece before you write "
    "stretch 1. The stretches are consecutive slices of the brief, IN ITS ORDER: "
    "stretch 1 begins where the brief begins and the last one ends where it ends. Never "
    "reorder it, never move its opening into the middle, and never spend a stretch on "
    "something it does not contain.\n"
    "SIZE THEM. The whole piece runs {total:.0f} seconds and about {chars} characters "
    "of spoken narration; each stretch gets roughly an equal share of both. So the "
    "material has to divide that way too — a stretch handed half the brief and a "
    "{share:.0f}-second slot is a stretch that cannot be written. If the brief holds "
    "more than the time allows, say so by cutting the least load-bearing material out "
    "of the plan entirely rather than by squeezing every stretch.\n"
    "For each stretch give:\n"
    '  • "covers": what it deals with, in order — 2-5 concrete sentences about events, '
    "claims and moments, not themes or mood. Written in {lang}.\n"
    '  • "details": the concrete things THE BRIEF puts in this stretch — its rules, its '
    "accounts, its names, numbers and quoted lines, in the brief's own wording. Where a "
    "detail needs a fact from the records to be said correctly (the world's own word "
    "for a thing, a name spelled right), add that fact here too — but the records never "
    "put an ITEM on this list of their own. This is the checklist its writer must "
    "spend: put each one in the single stretch it belongs to.\n"
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
        from ..drama import char_budget

        total = ctx.params.duration_s
        return OUTLINE_SYSTEM.format(
            wins=wins, lang=lang, part_rule=part_rule, part_json=part_json,
            world_rule=WORLD_RULE, premise_rule=PREMISE_RULE, brief_rule=BRIEF_RULE,
            total=total, share=total / max(wins, 1),
            chars=char_budget(total, ctx.params.lang, ctx.params.tts_rate),
        )

    def outline_user(self, ctx, *, brief, roster, beats, windows):
        """The brief comes FIRST and the records second, which is not cosmetic: handed
        forty thousand characters of lore and then a page of brief, the planner reads
        the brief as a note about which part of the lore to visit. It is the thing
        being cut up, so it opens the turn."""
        return (
            "THE BRIEF — this is the piece. Cut THIS up: its material, in its order, "
            "plus, where the operator addresses you directly, instructions to plan for "
            f"rather than to write down.\n{brief}\n\n"
            "THE RECORDS OF THIS WORLD — read them after the brief, and read them as "
            "the constraint on how it may be told: what this world contains, what its "
            "words are, what may not be contradicted. They are not the subject.\n"
            f"{self.lore}\n\n"
            "Characters who may appear — what they LOOK like, and nothing more. Not all "
            "of them are people, and an entry may be one figure, a body of identical "
            f"ones, or a whole kind:\n{roster}\n\n"
            f"The piece runs {beats} beats, cut into {len(windows)} stretches of "
            f"{', '.join(str(b - a) for a, b in windows)} beats."
        )

    # -- one window --------------------------------------------------------

    def window_system(self, ctx, w: Window, *, lang, roster=""):
        """One window's contract, ordered so that everything INVARIANT comes first.

        That order is the cheapest change in this file. A provider's prompt cache
        matches on the prefix and stops at the first difference, so the expensive
        constants — the canon sheet, the cast sheet, every rule above — used to be
        worth nothing to it: they sat BEHIND the per-window arc and part rules, and
        every window re-read all of them at full price, as did every retry. Everything
        that varies now lives in one block at the end (`window_rule`), where it cannot
        cost anything but itself."""
        template = (
            SYSTEM_CHRONICLER
            if ctx.params.fandom_voice == "chronicler"
            else SYSTEM_RESIDENT
        )
        tone = (ctx.fandom.tone if ctx.fandom else "").strip()
        # the varying tail, in one piece: how long this stretch runs, where in the
        # piece it sits, whether it opens the video, which of its beats close an episode
        window_rule = "\n" + shot_rule(
            w.clip_s, total=w.target_s, beats=w.beats, chars=w.chars,
            wps=w.words / max(w.clip_s, 0.1), photo=self.photo,
        ) + "\n" + w.arc + (OPEN_RULE_FANDOM if w.index == 0 else "") + w.part_rule
        return template.format(
            lang=lang,
            video_prompt_rule=VIDEO_PROMPT_RULE,
            world_rule=WORLD_RULE,
            cast_rule=CAST_RULE,
            premise_rule=PREMISE_RULE,
            world_block=self._world_block()
            + (f"\nHOW THIS ONE IS TOLD — the operator's note on register: {tone}\n"
               if tone else ""),
            roster_rule=ROSTER_RULE.format(roster=roster),
            brief_rule=BRIEF_RULE,
            window_rule=window_rule,
        )

    def window_user(self, ctx, w: Window, *, brief, roster, tail, lang):
        """The user turn is now only the brief and the handover, in that order.

        The cast sheet moved into the system prompt (see `window_system`), and the
        brief goes first here for the same reason: it is identical in every window, so
        keeping the one varying thing — the last lines already written — at the end
        leaves the whole prefix cacheable, retries included."""
        user = (
            "THE BRIEF — this is the piece you are writing. Its material, its order and "
            "its shape; and where the operator addresses you directly, instructions to "
            f"follow rather than to voice.\n{brief}\n\n"
            f"Write the narration in {lang}; keep every video_prompt in English.\n"
        )
        if tail:
            user += f"\nThe beats already written end like this:\n{tail}\n"
        return user

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
