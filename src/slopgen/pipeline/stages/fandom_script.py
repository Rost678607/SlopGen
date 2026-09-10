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

Three narrators are on offer, chosen per run (`params.fandom_voice`):

  * `resident` — someone who lives there, first person, the world as daily life. The
    drama's voice, pointed at a world instead of a plot.
  * `chronicler` — an archivist, researcher or crank OF that world, reading its
    records as real documents and building theories out of them. A video essay whose
    author happens to live inside its subject.
  * `usher` — speaks TO you, and the "you" is a person in the world: a new hand told
    how things are done here, warned about what the place costs, offered a choice
    between its prizes. This is the one voice that uses the second person, and the
    distinction it rests on is the whole reason it does not break the world: it
    addresses a colleague, never a viewer. "What you were before here will be
    forgotten" is inside the world; "as you can see in this video" is not.

The world reaches the writer in three layers, cheapest first, because none of them
works alone (see `llm/lore.py` for the full reasoning):

  1. the compiled canon sheet, in every window — an inventory, so the writer knows a
     thing exists even when it would never have thought to ask about it;
  2. the outline pass, which reads the whole lore once and hands each window the
     concrete facts that window is responsible for spending (`beats.DETAILS_RULE`);
  3. `lore_lookup`, the archivist tool, for the detail the writer knows it is missing
     — the only layer that costs a full reading of the documents per question.

What happens where all three layers are silent is the operator's to answer, per run
(`params.fandom_invent`, the slider next to the narrator), and it has three positions
rather than two:

  * `no` — the records are the whole world. A gap is something nobody knows, said as
    a fact about the place, and never filled with a specific of the writer's own.
  * `gaps` — invention is a REPAIR, not a licence: it happens only where the beat in
    hand cannot otherwise be written, and only as much of it as unblocks that beat.
  * `free` — the records are merely what somebody WROTE DOWN, and the writer furnishes
    the rest of the world at will, in its own grain.

The middle rung exists because the two-position version did not work, and the way it
failed is worth writing down. Told merely that it MAY invent, a model does not add a
lantern-maker's price. It invents the thing the piece is about. A measured run against
a world of mountain post-runners, with a brief asking about a term that world does not
contain, came back with a whole coinage — "so we call it the Object: the place that
answers" — a proper noun minted for the one thing the records deliberately leave open,
in a piece whose every other sentence was faithful. That is the shape of the failure
every time: the invention lands on the subject, on a name, or on the mystery, because
those are the places a gap is most keenly felt. So the limits in `INVENT_LIMITS` bind
BOTH licences — no subject, no proper noun, nothing near an open question, and nothing
the rest of the piece then leans on — and `gaps` adds the one thing that actually
holds the reflex down: a budget. Where you can write the beat without inventing, you
did not need to.

All three answers are right for different worlds, which is why this is a question and
not a rule — thin lore is unwritable under the first, and a world someone is genuinely
archiving is ruined by the third.

None of that decides what the VIDEO is, and for a long time nothing did. A piece of
this kind is not a small documentary about a world: it is one thing, opened in the
middle of itself, taken apart in an order where every sentence is caused by the last,
turned once near the end when the arrangement turns out to have a price, and stopped
on a line that explains nothing. That shape is written down twice here — as rules
every pass is held to (`PIECE_RULES`), and as this particular video's own plan, made
before a beat is written (`Spine`, `plan_spine`). The section above `PIECE_RULES` has
the measured failure that made both necessary.

The world's cast is not a fourth layer, and the mode is careful to say so (see
`CAST_RULE`). It is a WARDROBE: a list of what things look like, where one entry may be
one character, a body of identical faceless ones, or a whole kind of them. Everything a
character IS reaches the writer through the three layers above, like every other fact
about the world — which is the point, since a world's people are made of the same
material as its weather and its ledgers, and splitting them off into little character
sheets is how a video ends up about four people standing in a place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ...llm.tools import LORE_LOOKUP_TOOL, make_lore_lookup
from ..context import AppContext
from ..job import VideoJob
from .beats import (
    FIDELITY_RULE,
    MAX_BEAT_S,
    MIN_BEAT_S,
    PREMISE_RULE,
    Window,
    write_beats,
)

log = logging.getLogger(__name__)

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
    "{gap}"
    "If you speak to someone as 'you', that someone is STANDING IN THE WORLD — a new "
    "hand, a traveller, whoever the narration is aimed at. Never the person watching. "
    "The line is not about the pronoun, it is about where the listener is: 'what you "
    "were before you came here will be forgotten' is inside; 'as you can see' is not.\n"
)

# What happens where the records stop, which is the one question about this world the
# operator answers per run (`params.fandom_invent`) rather than once per world. Three
# answers, and all three are defensible for different worlds: a world whose lore is
# thin is unwritable under the first, and a world someone is genuinely archiving is
# ruined by the third — an invented price in a finished video is indistinguishable
# from a recorded one, and the operator who wrote the records is the only person who
# can tell.
GAP_NONE = (
    "WHAT THE RECORDS DO NOT CONTAIN, YOU DO NOT ADD: no name, no number, no custom, "
    "no place, no institution, no incident and no turn of phrase that its records give "
    "you no reason to believe exists. Where a gap is load-bearing, the honest line is "
    "that nobody knows — said in the world's own terms — and never a specific of your "
    "own, which a listener cannot tell from a recorded fact.\n"
)

# The limits, written once and attached to BOTH licences. Every clause is an observed
# failure of the two-position version, and they share one cause: told it may invent, a
# model spends the licence at the exact point where the gap hurts most — which is
# never the lantern-maker's price. It is the subject, the name, or the mystery.
INVENT_LIMITS = (
    "Five limits, and they are the whole of what makes this safe:\n"
    "  • NOTHING YOU ADD MAY CONTRADICT A RECORD. Where a record speaks it wins "
    "outright, and what you invent has to survive being read next to it.\n"
    "  • IT STAYS IN THE WORLD'S GRAIN — its materials, its scale, its craft, its "
    "institutions, its manners of speech. Ask what this place is made of and add only "
    "more of the same; a thing of a kind these records give you no reason to believe "
    "could exist here does not exist here.\n"
    "  • YOU NEVER INVENT WHAT THE PIECE IS ABOUT. The subject — the place, the "
    "custom, the event, the person the video is FOR — comes from the records, always. "
    "A licence to fill in texture is not a licence to manufacture the thing being "
    "described, and a piece resting on something you made up is a piece about nothing, "
    "however faithful every sentence in it sounds.\n"
    "  • YOU NEVER COIN A PROPER NOUN. No name for a place, a rite, a body, an office, "
    "an instrument or a phenomenon the records leave unnamed, and no term of art the "
    "world does not already use. Naming a thing is the strongest claim a narrator can "
    "make about it: a coinage is heard as the world's own word, and it cannot be told "
    "apart from one afterwards. Where a thing has no name here, describe it — 'the "
    "place under the ninth marker', not a word you invented for it.\n"
    "  • IT IS TEXTURE, NEVER REVELATION. You fill in what nobody bothered to write "
    "down — the ordinary, the small, the daily. You do NOT settle what the records "
    "leave open: a question this world argues about, a mystery nobody has solved, a "
    "thing a record pointedly does not say, all stay exactly as unsettled as they "
    "were, and you do not invent NEAR one either — a detail added beside an open "
    "question reads as a hint at its answer. Where not knowing is itself the fact, it "
    "stays the fact: nobody came back, the page for that winter is missing, she never "
    "told anyone. That is something the world CONTAINS, not a hole left for you to "
    "fill.\n"
)

# The bounded licence, and the default answer for most worlds. What makes it different
# from the free one is not a longer list of prohibitions — the list is the same — but a
# BUDGET: invention has to be earned by a beat that cannot be written without it, and
# the writer is asked to notice that this is almost never the case.
GAP_GAPS = (
    "WHERE THE RECORDS STOP, YOU MAY INVENT — BUT ONLY TO GET UNSTUCK. This is a "
    "repair, not a licence. Before you add anything, try writing the beat without it: "
    "nine times in ten the sentence works with the records' own material, or with one "
    "concrete detail less, and then you did not need to invent. Reach for it only when "
    "the beat genuinely cannot be written otherwise, and then add the SMALLEST "
    "ordinary thing that unblocks it — a habit, a price, a tool, the order things are "
    "done in — said flatly, in one clause, in passing. Nothing later in the piece may "
    "lean on it: if dropping your invention would break a beat further down, it was "
    "not texture and you should not have added it.\n"
    + INVENT_LIMITS
)

# The open licence: the records are a starting point and the writer furnishes the rest.
# For a world with three paragraphs of lore, which is unwritable under either of the
# other two. The limits above still bind it — they are not about how MUCH is invented.
GAP_FREE = (
    "THE RECORDS ARE WHAT WAS WRITTEN DOWN, NOT THE WHOLE WORLD, and you may know more "
    "of it than they do — the operator has asked you to. So where they stop and the "
    "piece needs something, invent it: a price, a custom, a tool, a street, someone's "
    "habit, the phrase people here use for a thing. Say it flatly, as fact, the way "
    "everything else here is said — never hedged into a guess ('perhaps', "
    "'possibly', 'some say') unless the doubt is itself the point.\n"
    + INVENT_LIMITS
)

GAP_RULES = {"no": GAP_NONE, "gaps": GAP_GAPS, "free": GAP_FREE}


def invents(invent: str) -> bool:
    """Whether this run's answer permits invention at all. Every caller that used to
    read the checkbox asks this instead, so a fourth rung would reach them all."""
    return invent in ("gaps", "free")


def world_rule(invent: str) -> str:
    """The world contract, with the operator's answer to the gap question in it."""
    return WORLD_RULE.format(gap=GAP_RULES.get(invent, GAP_NONE))


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

# The operator's note on register (`FandomConfig.tone`), and it used to arrive as a
# footnote: one unheaded line appended under the canon sheet, in the middle of a prompt
# whose every other rule pushes toward compression. What survives that treatment is the
# half of a tone note that agrees with the pressure — «ровный, канцелярский, короткими
# фразами» lands, and «охотно вставляет присказки и приметы», sitting in the same
# sentence, does not. The result reads exactly as flat as the note asked and carries
# none of what the note asked FOR, which is the operator's own voice half-applied.
#
# So it gets a heading, it is named as binding, and it says outright that a tone note
# has two halves. The last clause is the load-bearing one: told to fit a budget, a
# writer economises on texture first, because texture is the part that is not
# information. It is the part the operator is actually asking for.
TONE_RULE = (
    "\nHOW THIS ONE IS TOLD — the operator's note on register. Every word of it binds "
    "you, and it binds you in BOTH directions: what it says the voice avoids, the "
    "voice avoids; what it says the voice reaches for, the voice reaches for, in every "
    "beat that can carry one. A note is not a list of prohibitions with some scenery "
    "attached — the things it invites are the things the operator is asking to hear.\n"
    "{tone}\n"
    "The length budget never overrides this. When a beat will not fit, take a THING "
    "out of it — a fact, a step, a clause of business — never the register: a beat "
    "stripped to its information is not a shorter version of this voice, it is a "
    "notice board.\n"
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
#
# The newest of them is `BRIEF_UNKNOWN`, and it is the one the brief's own authority
# creates. A brief asking «Что такое Объект?» about a world holding no such word is a
# question with no answer in the records — and a writer told the brief IS the piece,
# and separately that it may invent, resolves the collision the obvious way: it mints
# the brief's word as a name of the world and writes three faithful beats about a thing
# that does not exist. The brief decides WHAT IS TALKED ABOUT; it has no power to
# create what is talked about, and once that is said outright the writer reaches for
# the real thing the brief was pointing at instead.
#
# What this rule no longer carries is HOW CLOSELY the brief is to be followed word for
# word — that question is not the world's, it is every mode's, and it lives one rule
# further down in `beats.FIDELITY_RULE`. What stays here is only what the records
# change about it: they are a constraint and never the material a gap gets filled with.
BRIEF_RULE = (
    "\nTHE BRIEF — THIS IS THE VIDEO, not a topic for one. The operator wrote it, and "
    "it is the spine of what you write: its material, its order and its shape. "
    "Everything in it is in the piece; nothing that is not in it is added to the piece. "
    "If it lists six things, you say all six, in its order, and you do not open with a "
    "seventh. If it moves from one kind of material to another — rules, then accounts, "
    "then a close — you keep that structure and let the beats fall where it turns. You "
    "are not summarising it and you are not taking inspiration from it: you are SAYING "
    "it, in this world's voice, cut into beats and fitted to the time. How far you may "
    "reword it, and where you may write material of your own at all, is the rule below "
    "this one; here it is enough that the brief is the piece.\n"
    "If it asks you to argue a THEORY, genuinely build one: lay out the evidence from "
    "the records, name what does not add up, and commit to a conclusion. It is a claim "
    "made INSIDE the world by someone who lives there — never a fan theory, never a "
    "reading of a text, never a guess about what an author meant. Say 'the ledgers "
    "disagree', never 'the lore is inconsistent'.\n"
    "THE RECORDS ABOVE ARE A CONSTRAINT, NOT MATERIAL. They say what this world "
    "contains, what its words are, and what nothing you write may contradict. They "
    "never add a subject the brief did not raise and never earn a beat of their own: "
    "you use them the way you use grammar — everywhere, and invisibly. "
    "{gap}"
    "{unknown}"
    " And where the records say a thing has never been seen clearly, "
    "it has not: neither the narration nor a video_prompt may show it plainly, and the "
    "shot is built around what people did see.\n"
    "The brief's own furniture is not narration. A heading, a numbering, a note about "
    "what the text is, a line marking where it stops ('that is the end of the "
    "instruction') — that is scaffolding you write TO, never text you read out. Nor is "
    "an instruction the operator addressed to you rather than to the world.\n"
)

# The second half of the gap question (see GAP_NONE / GAP_GAPS / GAP_FREE), asked where the
# brief meets the records rather than where the world runs out. The failure it is
# written against survives the licence intact: a reason ASSEMBLED out of the records
# reads, in the finished video, as a record saying something it never said — which is a
# different and worse thing than inventing openly out of the world.
BRIEF_GAP_STRICT = (
    "So they are never a reason to fill a gap either: where the brief says a rule is "
    "kept and does not say why, then why is not known, and you say THAT — not a reason "
    "the records let you assemble."
)
BRIEF_GAP_INVENT = (
    "So they are not what a gap gets filled with either. A reason ASSEMBLED out of "
    "them — 'the ledger lists six, so the sixth must be the one they fear' — is "
    "inference wearing a record's coat, and it is not what you were licensed to do. "
    "What you may do in a gap is invent, openly, out of the world, as a plain fact of "
    "the place, as far as the gap rule above allows and only where the brief leaves "
    "the writing to you (the rule below says where that is)."
)


# The brief's authority runs to the SUBJECT and stops there. Nothing above says so,
# and the omission is expensive: a brief naming a thing this world does not hold reads,
# to a writer told the brief is the piece, as proof that the thing exists — after which
# every rule here is obeyed to the letter around a fabricated centre. This clause binds
# under all three gap answers, the free one included: a licence to furnish a world is
# not a licence to install the operator's word in it as a name.
BRIEF_UNKNOWN = (
    "\nA WORD IN THE BRIEF IS NOT A FACT OF THE WORLD. The brief decides what is "
    "talked about; it cannot bring a thing into existence. So where it names something "
    "the records do not hold — a place, a term, an institution, a rite, a person — you "
    "do not create it, whatever else you are permitted to invent. Ask what the brief "
    "is POINTING AT and answer about that, in this world's own word for it: a brief "
    "asking about a thing under a name nobody here uses is almost always asking about "
    "something real under the wrong name, and the piece is written about the real one. "
    "If the records hold nothing the brief could mean, say so plainly and inside the "
    "world — no such thing is known here, nobody uses that word — and spend the piece "
    "on the nearest thing that is real. Never adopt the brief's word as a name this "
    "world uses, and never build the piece on a thing you had to invent to have a "
    "subject at all.\n"
)


def brief_rule(invent: str) -> str:
    """The brief contract, with the same answer carried through to the records."""
    return BRIEF_RULE.format(
        gap=BRIEF_GAP_INVENT if invents(invent) else BRIEF_GAP_STRICT,
        unknown=BRIEF_UNKNOWN,
    )


# --------------------------------------------------------------------------
# The shape of a piece
# --------------------------------------------------------------------------
#
# Everything above this line makes the SENTENCES right and none of it makes the VIDEO
# one thing, which is the whole of what was wrong with this mode. Measured, on a
# thirty-second run against a world of institutional corridors, brief «Первый день на
# Объекте»: three beats came back, every one of them faithful, in the world's own
# words, unimprovable line by line — a checkpoint and a form, then a briefing document,
# then a piece of advice about a tally mark. Three true things about the same subject,
# with nothing following from anything. The operator's word for it was that it jumps
# from subject to subject, and it does; but no rule in this file was broken, because
# no rule in this file was about the piece.
#
# Why the gap was invisible: the outline pass is this mode's only planning, and
# `beats.outline` returns immediately below two windows — fourteen beats. Every short
# video therefore had NO plan at all, and got `beats.ARC_WHOLE` instead, which offers
# a drama's arc (hook → rise → turn → payoff) to a piece that has no plot. Meanwhile
# `FIDELITY_RULE` reads a four-word brief as case 3, a sketch, and hands the writer a
# free licence over all three beats. Free licence, no plan, no shape: three stabs at
# the topic is the only thing that could have come out.
#
# What replaces it is measured off the pieces this mode is actually trying to be —
# the short in-world videos that work, watched and taken apart. They are not built
# like small documentaries. Every one of them is ONE thing, opened in the middle of
# itself, taken apart in an order where each sentence is caused by the last, turned
# once near the end when the arrangement turns out to have a price, and stopped on a
# line that explains nothing. Sixty words, one subject, no survey. That is a shape,
# and it can be said in rules (`PIECE_RULES`) and decided per video (`Spine`).
#
# The spine is also where the mode gets back something the short pieces never had:
# a pass that reads the WHOLE records against the brief before a beat is written.
# That was layer 2 of the three (see the module docstring), and below fourteen beats
# it simply did not run.

# The five rules, and they are the piece rather than the world. Each is a test rather
# than a taste: a writer cannot tell whether its piece "flows", and it can tell
# whether two beats can be swapped without breaking anything.
PIECE_RULES = (
    "\nHOW A PIECE OF THIS KIND IS BUILT — six rules, and together they are what "
    "makes one video instead of several true things said in a row.\n"
    "  • ONE THING. The whole piece is about ONE thing, and the first beat names it. "
    "Everything after that is still that thing. The test: lift any beat out and drop "
    "it into a different video about this world — if nothing notices, it did not "
    "belong in this one. Breadth is the enemy here: a subject you could say four "
    "unrelated true things about is four videos, and you are writing one of them.\n"
    "    And the thing is HAPPENING, not being characterised. 'A first day here is "
    "always the same' is a remark ABOUT first days, made from outside all of them; "
    "this piece is one of them, going on now, to the person listening. Nothing in it "
    "is 'always', 'usually', 'as a rule' or 'every time' where it could simply be "
    "this time — a general truth about the subject is the driest sentence available "
    "and it puts the listener outside the very thing you are putting them inside.\n"
    "  • A CHAIN, NOT A LIST. Every beat after the first follows FROM the one before "
    "it — because of it, in spite of it, as its price, as what somebody does about "
    "it. The test: swap any two beats. If nothing breaks, you wrote a list, and a "
    "list is exactly what jumping from subject to subject is.\n"
    "  • ONE TURN, AND IT COMES LATE. In the last third the thing turns: the "
    "arrangement has a price, the courtesy is a count, the rule is protecting "
    "somebody else. ONE turn — a piece that turns in every beat has no turn at all — "
    "and it is not a twist you invent, it is the part of the thing everybody there "
    "has stopped noticing.\n"
    "  • THE LAST BEAT DOES NOT EXPLAIN. It is the shortest, and its job is to STOP: "
    "a warning, an instruction, a question nobody there answers, one flat sentence "
    "that lands. Never a summary of what was just said, never a moral, never a line "
    "telling the listener what they have heard.\n"
    "  • THIS WORLD'S WORDS, IN EVERY BEAT. At least one thing named the way the "
    "records name it, every time, and never glossed — no 'so-called', no 'that is to "
    "say', no explaining a word to somebody who lives here. Those names are most of "
    "what makes a piece sound like it came from somewhere.\n"
    "  • A BEAT IS NOT A NOTICE. A sentence carrying nothing but who did what to what "
    "is a line off a notice board, and a run of them is how a piece can be true in "
    "every particular and still unlistenable. So every beat carries ONE thing besides "
    "its fact — one, not all of them, and never a whole beat spent on it:\n"
    "      – what the listener's hands, feet or eyes are doing while it happens;\n"
    "      – what they are thinking, hoping, dreading or already spending;\n"
    "      – a measure in this world's own units, where it has them;\n"
    "      – a saying, an omen, a superstition or a piece of advice people here "
    "repeat to each other;\n"
    "      – one physical detail nobody needed to mention.\n"
    "    The register note above decides WHICH of these this world likes; it does not "
    "decide whether there is one. A flat voice is not an empty one — flatness is how "
    "the strange things are said here, not a reason to say only the necessary ones.\n"
    "Where the operator has already WRITTEN the piece, its shape is the piece's shape "
    "and it outranks all six.\n"
)

# The four shapes, and they are a closed list on purpose. Asked to pick a form freely
# a model picks "an atmospheric exploration of", which is the survey again under a
# better name. Each of these four has a spine that cannot be written as a list.
SPINE_SHAPES = (
    "\nFOUR KINDS OF PIECE. One of them fits what the brief asks for better than the "
    "others; pick it and say which:\n"
    "  • MECHANISM — one arrangement of this world taken apart: what it is, how it is "
    "actually done, and what it costs the people it is done to.\n"
    "  • DUTIES — somebody has been put somewhere, and this is what that means for "
    "them: what they will do, in what order, and what it earns them.\n"
    "  • RULE — a thing that must be done a certain way here: the conditions under "
    "which it holds, and what reaches you when it does not.\n"
    "  • VIGNETTE — one named someone wants one concrete thing, and the piece is the "
    "getting of it: what is in the way, what they try, where it stands when the time "
    "runs out. It ends unfinished.\n"
)

# The planner. It writes no narration at all, and saying so twice is not redundant:
# handed a world and a topic, a model's first instinct is to start the video.
SPINE_SYSTEM = (
    "You are planning ONE narrated vertical video set in the world whose records are "
    "given below. You write not a single line of it. You decide WHAT THE VIDEO IS "
    "ABOUT and the order in which that one thing comes apart; a writer gets your plan "
    "and nothing else of yours.\n"
    "{world_rule}"
    "{world_block}"
    "{role_rule}"
    "{piece_rules}"
    "{shapes}"
    "SIZE IT. The finished video runs about {total:.0f} seconds — {beats} beats of "
    "narration in all, and no more. That decides how big a subject may be: one "
    "arrangement told properly, never a history and never a tour. If what you have in "
    "mind will not fit, it is the wrong subject, or it is two of them and you take "
    "the better one.\n"
    "Give the plan as:\n"
    '  • "subject": the ONE thing the video is about, in this world\'s own words, in '
    "one line. A thing, an arrangement, a rite, a job, a place, one person's one "
    "habit — never a theme, never a period, never 'life at' somewhere.\n"
    '  • "shape": which of the four kinds above it is.\n'
    '  • "open": what is HAPPENING, said flatly in one line, the way somebody there '
    "would say it to the person it is happening to. Not an introduction to the world "
    "and not a mood — the situation, named. Whoever hears the first line must know "
    "within those few words what this is about; a piece that opens on an atmospheric "
    "detail and leaves the subject to be worked out has wasted its opening.\n"
    '  • "steps": 3-6 short lines, IN ORDER, each following from the one before it: '
    "how the thing actually works. Concrete throughout — what is done, by whom, with "
    "what, how often, at what price — and every fact in them comes from the records.\n"
    "    Spend the ORDINARY first. Where somebody is put, what they are given, what "
    "they do there all day, what is expected of them: that is the material, and it is "
    "the material a listener has to have before anything can be worth turning. A plan "
    "that spends all its steps on getting somewhere and none on being there has "
    "described a corridor.\n"
    '  • "turn": what follows from THE LAST STEP and is worse, stranger or costlier '
    "than the steps sounded. One thing, and it has to GROW OUT of them — read your "
    "last step and your turn together, and if the turn introduces something the steps "
    "never touched, then either the steps are the wrong ones or the turn is, and you "
    "fix that here rather than leaving the writer to bridge it.\n"
    '  • "close": what the last line does — the warning, the instruction, the '
    "question nobody answers. One line, and it explains nothing.\n"
    "Write all of it in {lang}. This is a plan, not narration: no beats, no seconds, "
    "no shot descriptions, and nothing written out in the narrator's voice.\n"
    'Respond with JSON only: {{"subject": "...", "shape": "...", "open": "...", '
    '"steps": ["...", "..."], "turn": "...", "close": "..."}}.'
)

# The plan as the writer sees it. The line about steps not being beats is load-
# bearing: given five steps and three beats a writer will write five beats, and given
# three steps and six beats it will pad each one out into two.
SPINE_RULE = (
    "\nTHE SPINE OF THIS PIECE, settled before you were called. This is its subject "
    "and its order, and it is not a suggestion.\n"
    "  THE SUBJECT: {subject}\n"
    "  WHAT KIND OF PIECE: {shape}\n"
    "  IT OPENS INSIDE: {opens}\n"
    "  THEN, IN THIS ORDER:\n{steps}\n"
    "  THE TURN, LATE: {turn}\n"
    "  IT STOPS ON: {close}\n"
    "The steps are the ORDER of the piece and not its beats: one step may take two "
    "beats, and two small steps may share one. Spend them all, in that order, and add "
    "no step of your own — a subject the spine does not name is a subject this video "
    "is not about. If they will not all fit, drop the least load-bearing step whole "
    "rather than compressing every one of them.\n"
)

# A brief this short is a line off a queue (see `llm.lore.SHAPE_TOPIC`) or a phrase
# the operator typed into the wizard. Either way it names a subject and stops, and the
# writer has to be told that — `FIDELITY_RULE` case 3 says the writing is yours, which
# is true and reads, without this, as permission to cover the topic.
TOPIC_CHARS = 160
BRIEF_TOPIC = (
    "\nTHE BRIEF IS A TOPIC, NOT A TEXT. What the operator wrote names the SUBJECT of "
    "this video and stops. It is not the content, and its words are not lines to "
    "voice — so the rule above about keeping the operator's sentences has almost "
    "nothing to keep, and what it binds you to is the subject alone.\n"
    "Do not answer a topic with a SURVEY. Three true things about it, one per beat, "
    "is what a topic pulls out of a writer, and it is the one failure this kind of "
    "piece has: nothing follows from anything and the video ends without having been "
    "about anything. Find in the records the ONE arrangement, custom, procedure, day "
    "or moment inside that topic which fills the whole video by itself, and spend the "
    "whole video on it. A topic is where you start looking, not what you must cover.\n"
)

# A single window is the whole video, and it has to be told so in this mode's terms
# rather than the drama's (`beats.ARC_WHOLE` offers it a plot's arc, which is the
# wrong shape and the wrong vocabulary for a piece with no plot in it).
ARC_FANDOM = (
    "You are writing the WHOLE piece — all {beats} beats of it, with no second writer "
    "to carry anything on. Its shape is the one the rules above describe: open inside "
    "the subject, work through it in order, turn once near the end, stop on the "
    "closing line. The last beat is the last of the video: nothing follows it, so it "
    "neither hands over, nor sums up, nor trails off.\n"
)

# How long a brief may be and still want a spine. Above this the operator has written
# the piece, and a written piece is CUT UP rather than re-planned (`FIDELITY_RULE`
# case 1, and the same reasoning as `beats.outline`'s docstring): planning a shape for
# a text that already has one is how a writer talks itself into improving it.
SPINE_MAX_BRIEF = 600


@dataclass
class Spine:
    """One video's plan: what it is about, and the order it comes apart in."""

    subject: str
    shape: str = ""
    opens: str = ""
    steps: list[str] = field(default_factory=list)
    turn: str = ""
    close: str = ""

    def block(self) -> str:
        return SPINE_RULE.format(
            subject=self.subject, shape=self.shape or "—", opens=self.opens or "—",
            steps="\n".join(f"    {i + 1}. {s}" for i, s in enumerate(self.steps)),
            turn=self.turn or "—", close=self.close or "—",
        )


def plan_spine(ctx: AppContext, writer: "FandomWriter", *, brief: str, beats: int,
               lang: str) -> Spine | None:
    """Decide what this video is about, once, before any of it is written.

    Returns None when the answer is unusable, and the piece is then written under
    `PIECE_RULES` alone — which is worse but perfectly writable, and a good deal
    better than a run that dies because an optional pass came back malformed.

    The records go in WHOLE rather than as the compiled sheet, for the reason the
    outline gives: the sheet is one line per thing, and a spine is made of exactly the
    detail a one-line inventory drops."""
    from ..drama import char_budget
    from ...llm.client import LLMError

    system = SPINE_SYSTEM.format(
        world_rule=world_rule(writer.invent), world_block=writer.spine_world(ctx),
        piece_rules=PIECE_RULES, shapes=SPINE_SHAPES, lang=lang,
        # The planner has to know WHO IS BEING SPOKEN TO, and leaving it out cost the
        # first measured piece its whole register. Planning «первый день» for nobody
        # in particular produces a procedure — a thing that is done, by unnamed people,
        # to whoever — and a writer handed a plan in that voice writes «ведут в Первый
        # отдел» where the listener should have been. The same block the outline gets.
        role_rule=outline_role_rule(ctx),
        total=ctx.params.duration_s, beats=beats,
    )
    user = (
        "THE BRIEF — what the operator asked for. It says what the video is about; it "
        f"does not say how the video goes, and that is what you are for.\n{brief}\n\n"
        "THE RECORDS OF THIS WORLD — read them for the one thing inside that brief "
        "that will hold a whole video, and for the concrete detail that makes it hold "
        f"one.\n{writer.lore}\n\n"
        f"The video runs {ctx.params.duration_s:.0f} seconds — about "
        f"{char_budget(ctx.params.duration_s, ctx.params.lang, ctx.params.tts_rate)} "
        "characters of narration in all."
    )
    try:
        data = ctx.llm.complete_json(
            f"{writer.kind}_spine", system, user, tools=writer.tools(ctx)
        )
    except LLMError as e:
        log.warning("no spine for this piece (%s) — writing it without one", e)
        return None
    subject = str(data.get("subject") or "").strip()
    steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
    # a subject and an order are the two things the writer cannot make up for itself;
    # a spine missing either is not a plan, it is a topic said twice
    if not subject or len(steps) < 2:
        log.warning("the spine came back without a subject or an order — writing "
                    "this piece without one")
        return None
    return Spine(
        subject=subject, shape=str(data.get("shape") or "").strip(),
        opens=str(data.get("open") or data.get("opens") or "").strip(), steps=steps,
        turn=str(data.get("turn") or "").strip(),
        close=str(data.get("close") or "").strip(),
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
    "{fidelity_rule}"
    "{piece_rule}"
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
    "{fidelity_rule}"
    "{piece_rule}"
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


# --- who "you" is, for the usher voice -------------------------------------
#
# The voice needs an addressee before it can say a single sentence, and there are three
# places that answer could come from, in falling order of authority: the run (`params.
# viewer_role`), the world (`FandomConfig.viewer_role`), and — when neither is set —
# the records themselves. The last one is not a fallback so much as the normal case:
# the canon compiler already works out WHO A NEWCOMER HERE BECOMES from any lore handed
# to it, so a world nobody has annotated still addresses the same person in every beat
# instead of a different one per window.
#
# What the role fixes is the ADDRESSEE, never the subject. That distinction is the
# whole value of the setting: a piece told to a clerk can be about the floor above
# them, about a promotion they have not been offered, about a room they will never be
# let into — it is simply told from where they stand, as what they would hear of it and
# what it would cost them to go and look. Without the line below, a writer handed a
# subject above its listener's station quietly promotes the listener to reach it.
ROLE_RULE = (
    "\nWHO 'YOU' IS: {role}\n"
    "That is the person you speak to, for the whole piece, and it overrides anything "
    "the records might suggest instead. Never swap them for someone else partway "
    "through, and never widen them into everyone here.\n"
    "It fixes WHO IS LISTENING, not what the piece is about. Where the material sits "
    "above them, beside them or somewhere they have never been let into, tell it TO "
    "them from where they stand: what reaches them of it, what it would mean for them, "
    "what it would cost them to go and see. Do not promote them to reach the subject, "
    "and do not shrink the subject to what they already know.\n"
)
ROLE_INFER = (
    "\nWHO 'YOU' IS is not stated, so settle it before you write and keep it for the "
    "whole piece. Read the records for the position a person newly arrived here ends "
    "up in — what they are called, what they are given, what they are set to doing — "
    "and speak to THAT person throughout. Take the commonest position, not a "
    "remarkable one. Where the records describe no arrival, take the position a person "
    "here would be assumed to hold unless told otherwise.\n"
    "Whatever you settle on, it fixes WHO IS LISTENING, not what the piece is about: "
    "material from above or beside them is told TO them from where they stand, not by "
    "moving them to it.\n"
)


# Neither planner writes a sentence, so the same block has to arrive saying what an
# addressee changes about a PLAN: the shape of its parts, not their wording. Both of
# them use it — the outline that cuts a long piece into stretches, and the spine that
# decides what a piece is at all — so it names neither.
OUTLINE_ROLE_LEAD = (
    "\nTHE PIECE IS SPOKEN TO ONE PERSON STANDING IN THIS WORLD, so plan it as "
    "something said to them: what they will need, what will happen to them, what they "
    "must not do, what they may choose. Anything in the plan that merely describes the "
    "world is planned for the wrong voice.\n"
)


def outline_role_rule(ctx) -> str:
    block = role_rule(ctx)
    return OUTLINE_ROLE_LEAD + block if block else ""


def role_rule(ctx) -> str:
    """The addressee block for the usher voice, and nothing at all for the other two —
    a resident's "I" and a chronicler's reader are already settled by their prompts."""
    if ctx.params.fandom_voice != "usher":
        return ""
    role = (ctx.params.viewer_role
            or (ctx.fandom.viewer_role if ctx.fandom else "")).strip()
    return ROLE_RULE.format(role=role) if role else ROLE_INFER


SYSTEM_USHER = (
    "You are writing a narrated vertical video, in {lang}, spoken TO one person by "
    "someone who has been in this world far longer than they have. The listener is "
    "IN the world — a new hand, a passer-by, someone who has just arrived and does "
    "not yet know how things are done. You are telling THEM.\n"
    "So the second person is the spine of it: what they will need, what will happen "
    "to them, what they must not do, what they may choose. Instructions, warnings and "
    "invitations, in that register — 'you will need', 'be careful, though', 'choose "
    "wisely'. An imperative is welcome. A rhetorical question aimed at them is "
    "welcome.\n"
    "KEEP THEM IN THE SENTENCE. The listener is not merely the reason for the piece, "
    "they are its grammar: they are done to, given to, taken from, sent, expected of. "
    "A subjectless plural — 'they take you through', written as 'is taken through' or "
    "as a bare 'take through' where the language allows it, 'the form is filled in', "
    "'a desk is issued' — is the world describing itself, and it is a different voice "
    "from this one. Every such sentence has a person in it: put them back. Say who "
    "does it to them, or say it happens TO THEM, or give the line as the instruction "
    "or duty it actually is — a list of what somebody must do is spoken as duties, in "
    "the form this world would give an order in, never as a report of what unnamed "
    "people are doing nearby.\n"
    "The 'you' is ALWAYS a person standing in this world. It is never someone "
    "watching a video, never a reader, never a subscriber, never an audience. That "
    "single distinction is what keeps this voice inside the world, and it is not "
    "negotiable: the moment 'you' means the person holding a phone, everything else "
    "in this prompt has been wasted.\n"
    "{role_rule}"
    "The voice knows the place cold and says the strangest things about it flatly, as "
    "arrangements everyone here has long since stopped questioning. It does not "
    "explain what a word means; it uses it. It does not soften what the place costs.\n"
    "Where the piece offers a choice, the options are a plain list of the world's own "
    "things, named and not described, and the piece ENDS on that list — no summary "
    "after it, no invitation to answer anywhere.\n"
    "{world_rule}"
    "{world_block}"
    "{roster_rule}"
    "{cast_rule}"
    "{brief_rule}"
    "{fidelity_rule}"
    "{piece_rule}"
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
#
# Which is why this rule used to overshoot, and the overshoot cost a measured piece
# its first beat. Told never to name or situate anything, a writer opens on an
# atmospheric detail — an old man at a turnstile looking at you as though for the
# first time — and the listener spends the beat working out what they are being told
# about. Naming the WORLD is the banned thing. Naming the SITUATION is the opposite of
# it, and it is what the openings that work actually do: they say flatly what is
# happening to you, in four or five words, and then are already inside it.
OPEN_RULE_FANDOM = (
    "FIRST BEAT — SAY WHAT IS HAPPENING, THEN BE INSIDE IT. Its opening words name "
    "the situation the piece is about, flatly, in this world's own words and as "
    "something that is happening to the person listening ('your first day at X', "
    "'they have put you in Y', 'today is Z'). Somebody who hears only that first line "
    "must already know what this is about. Then, in the same beat, you are inside it: "
    "one concrete moment, object or claim, no run-up (1-2 punchy sentences in all).\n"
    "Name it as THIS one, happening. 'Your first day' — never 'a first day here is "
    "always the same', which is a remark about first days in general, made from "
    "outside every one of them, and it puts the listener outside the day you are "
    "about to walk them through.\n"
    "What is forbidden is naming, introducing or situating THE WORLD — 'let me tell "
    "you about', a sentence written for someone who has never been here, anything "
    "explaining where we are. Saying what is happening is not that: it is the flattest "
    "line in the piece, and it is addressed to somebody who lives here.\n"
    "Its video_prompt must be visually arresting — dynamic framing, high contrast.\n"
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
    "{role_rule}"
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
    "The rules below are addressed to the writers, and they bind you first: an "
    "instruction the operator wrote TO them is never material to plan a stretch around, "
    "and a brief that is already written is cut up rather than re-planned — a "
    "\"covers\" that recounts things the brief does not contain is an invention its "
    "writer will dutifully put on screen.\n"
    "{premise_rule}"
    "{fidelity_rule}"
    "{piece_rule}"
    'Respond with JSON only: {{"title": "<short title in {lang}>", "stretches": '
    '[{{"covers": "...", "details": ["...", "..."], "ends_on": "..."}}, ...]{part_json}}}.'
)


class FandomWriter:
    """The fandom's half of the beat contract (see `beats.Writer`)."""

    kind = "fandom"
    fallback_title = "Хроника"
    self_timed = True  # the writer sizes every shot (see SHOT_RULE below)

    def __init__(self, canon: str, lore: str, lore_tool: bool, photo: bool = False,
                 invent: str = "no"):
        self.canon = canon
        self.lore = lore
        # how far the writer may add to this world where its records stop: "no",
        # "gaps" or "free" (see GAP_RULES)
        self.invent = invent
        # the tool earns its cost only when the records hold more than the sheet does
        self.lore_tool = lore_tool and bool(canon)
        # a slideshow is written differently from a run of clips: a still cannot hold
        # an action, so the shot descriptions have to be photographs (see SHAPE_PHOTO)
        self.photo = photo
        # what this video is about and the order it comes apart in, settled by
        # `prepare` before a beat is written — None when the brief already is the
        # piece, or when the pass came back unusable (see `plan_spine`)
        self.spine: Spine | None = None
        # whether the brief names a subject rather than carrying one (BRIEF_TOPIC)
        self.topic = False

    # -- what the writer is told about the world ---------------------------

    def _world_block(self) -> str:
        """The world itself: the compiled sheet, or the records whole when they are
        short enough that compiling them would save nothing."""
        if self.canon:
            block = CANON_RULE.format(canon=self.canon)
        else:
            block = LORE_RULE.format(lore=self.lore)
        return block + (LORE_TOOL_RULE if self.lore_tool else "")

    def spine_world(self, ctx: AppContext) -> str:
        """What the SPINE pass is told about the world.

        The sheet, never the raw lore: the records themselves go in that pass's user
        turn whole, and paying for both would be paying twice for the same world.

        The tone note comes too, and it is not decoration here. A register that
        welcomes sayings and omens is a register that wants sayings and omens PLANNED
        — they are material, and a plan that gathered none leaves the writer nothing
        to be that voice out of."""
        tone = (ctx.fandom.tone if ctx.fandom else "").strip()
        block = CANON_RULE.format(canon=self.canon) if self.canon else ""
        return block + (TONE_RULE.format(tone=tone) if tone else "")

    # -- the shape of this particular piece --------------------------------

    def prepare(self, ctx: AppContext, *, brief: str, beats: int, lang: str) -> None:
        """Settle what the video is about, before `beats.write_beats` writes any of it.

        Called for every fandom run, including the short ones the outline pass never
        reaches — which is the point of it, since those were the runs with no plan of
        any kind (see the section above)."""
        text = brief.strip()
        self.topic = 0 < len(text) <= TOPIC_CHARS
        if len(text) > SPINE_MAX_BRIEF:
            return  # the operator wrote the piece; it is its own spine
        self.spine = plan_spine(ctx, self, brief=brief, beats=beats, lang=lang)
        if self.spine:
            log.info("this one is about: %s (%s)",
                     self.spine.subject, self.spine.shape or "no shape given")
            ctx.progress("spine", 1, 1)

    def piece_rule(self) -> str:
        """The shape block every pass gets: what kind of brief this is, how a piece of
        this kind is built, and this particular one's spine."""
        return ((BRIEF_TOPIC if self.topic else "") + PIECE_RULES
                + (self.spine.block() if self.spine else ""))

    def whole_arc(self, ctx: AppContext, *, beats: int) -> str:
        """One window is the whole video (see `beats.write_beats`)."""
        return ARC_FANDOM.format(beats=beats)

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
            world_rule=world_rule(self.invent), premise_rule=PREMISE_RULE,
            role_rule=outline_role_rule(ctx),
            brief_rule=brief_rule(self.invent), fidelity_rule=FIDELITY_RULE,
            piece_rule=self.piece_rule(),
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
            {"chronicler": SYSTEM_CHRONICLER, "usher": SYSTEM_USHER}
            .get(ctx.params.fandom_voice, SYSTEM_RESIDENT)
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
            world_rule=world_rule(self.invent),
            role_rule=role_rule(ctx),
            cast_rule=CAST_RULE,
            premise_rule=PREMISE_RULE,
            world_block=self._world_block()
            + (TONE_RULE.format(tone=tone) if tone else ""),
            roster_rule=ROSTER_RULE.format(roster=roster),
            brief_rule=brief_rule(self.invent),
            fidelity_rule=FIDELITY_RULE,
            piece_rule=self.piece_rule(),
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
        invent=ctx.params.fandom_invent,
    ))
