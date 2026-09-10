"""Compiling a fandom's lore documents into the canon sheet the writer works from.

This is to a world what `llm/characters.compile_character` is to a character: a
prompt-engineering pass, not a summary. The operator writes lore as prose, in
whatever order it came to them; the writer needs it as a dense reference it can hold
in front of itself for every one of ~18 script windows — every proper noun, every
rule, every date, and above all every TABOO, so it never furnishes the world with
something that does not exist in it.

Why a compiled sheet at all, rather than just handing the writer the documents or a
search tool over them:

  * pasting the raw lore into all ~18 windows costs 18 full readings of it, and
    long prose spends the model's attention on narration it does not need;
  * a search tool alone (see `tools.make_lore_lookup`) has the classic retrieval
    failure — the writer never asks about the currency it does not know exists.

The sheet fixes the second problem cheaply: it is an INVENTORY, so the writer sees
that a thing exists even when it does not know to ask, and can then ask the archivist
for the detail. Freshness is a checksum of the documents rather than a dirty flag,
because lore is comfortably written in an outside editor (see
`config.models.FandomConfig`).

What the sheet buys is structure, not brevity, and it is worth being clear about that:
the shipped example world compiles from 11.5k characters of lore to a 10.6k-character
sheet — barely smaller. Densely written lore is already close to an inventory, so
there is little prose left to squeeze out. The sheet still earns its place three times
over: it is a checklist rather than a narrative, so the writer can scan it instead of
re-reading a story; it is identical in every window, which is what a provider's prompt
cache wants; and its `taboos` section does not exist in the source at all. Prose-heavy
lore — scenes, description, dialogue — does compress, and that is where the token
saving actually shows up.
"""

from __future__ import annotations

from ..config.loader import lore_sha
from ..config.models import FandomConfig

# The compile prompt wants a language the model recognises by NAME, and its callers
# disagree on what they have: the pipeline resolves the run's language through
# `stages.idea.LANG_NAMES` first, while the TUI config pane only knows the UI language
# code, having no run to take one from. Rather than make either of them care, accept
# both — anything already spelled out passes through untouched.
_LANG_NAMES = {"en": "English", "ru": "Russian"}


def _lang_name(lang: str) -> str:
    return _LANG_NAMES.get((lang or "").strip().lower(), lang or "English")

SYSTEM = (
    "You are the archivist of the world described below, compiling its records into "
    "ONE reference sheet for a narrator who will speak about this world as a real "
    "place they live in. The sheet is the only thing they hold while writing, so what "
    "you leave out effectively does not exist.\n"
    "This is an INVENTORY, not a retelling: name everything the records name, in the "
    "records' own words, and keep each entry to a line. Do not summarize the prose, do "
    "not interpret it, do not invent anything the records do not contain. Proper nouns, "
    "numbers and dates must be reproduced EXACTLY as written.\n"
    "TWO THINGS WITH THE SAME NAME ARE TWO ENTRIES. Where the records give more than "
    "one faction, region or house an office, a body, a custom or a rank of the SAME "
    "NAME — each side's own version of it — never fold them into one line, and never "
    "let a detail belonging to one attach itself to the other. Write an entry per "
    "version, say WHOSE it is, and say what that one does that the other does not. "
    "This is the single most damaging thing a sheet can get wrong: everything "
    "downstream reads it as fact, so one side ends up wearing the other's practice and "
    "nothing later can tell that it is wrong. When in doubt, split rather than merge.\n"
    "The world is real and these are its records. Never describe it as fiction, a "
    "story, a setting or someone's work; never mention an author, a canon or an "
    "audience.\n"
    "Give:\n"
    '  • "premise": what this world is, in 2-3 sentences.\n'
    '  • "rules": how it works — the laws, limits and customs a story set here may not '
    "break. One line each.\n"
    '  • "glossary": the world\'s own terms and what each means, one line each. Every '
    "word the records use that an outsider would not know belongs here. A word that "
    "means something different on either side of a border gets a line per side.\n"
    '  • "figures": named characters — who or WHAT they are and what they are known '
    "for. Not only people: a named beast, machine, vessel or building belongs here too "
    "if the records treat it as someone rather than something.\n"
    '  • "places": named locations, one line each.\n'
    '  • "factions": groups, guilds, families, institutions and what they want. Where '
    "two sides each run something of the same name, they are two lines here, each "
    "naming its side and its own way of going about it.\n"
    '  • "timeline": recorded events in order, each with whatever dating the records '
    "give.\n"
    '  • "taboos": what does NOT exist in this world and what never happens in it — '
    "absent technology, absent institutions, words nobody here would say, things "
    "outsiders would wrongly assume. Infer these from what the records show: a world of "
    "footpaths and lanterns has no cars and no telephones. This list is what stops the "
    "narrator furnishing the world out of our own.\n"
    '  • "register": how people here talk — vocabulary, formality, what they '
    "understate. One or two sentences.\n"
    '  • "viewer_role": who a person newly arrived in this world becomes — what they '
    "are called, what they are given, what they are set to doing, and what they may "
    "and may not become from there. This is the position ANY of these records could be "
    "explained TO, so give the commonest one rather than a remarkable one: the rank "
    "most people here hold, not the one the stories are about. Where the records "
    "describe no arrival at all, say instead what position a person here would be "
    "assumed to hold unless told otherwise. One or two sentences, in the records' own "
    "words, and name the term this world calls that person by.\n"
    "Write every field in {lang}, except proper nouns and terms, which keep the "
    "records' spelling.\n"
    'Respond with JSON only: {{"premise": "...", "rules": ["..."], "glossary": ["..."], '
    '"figures": ["..."], "places": ["..."], "factions": ["..."], "timeline": ["..."], '
    '"taboos": ["..."], "register": "...", "viewer_role": "..."}}.'
)

# Rendered order = reading order for the writer: what the world IS, then what binds
# it, then its nouns, then what it must never contain.
_SECTIONS = [
    ("premise", "WHAT THIS WORLD IS"),
    ("register", "HOW PEOPLE HERE TALK"),
    # Who any of this could be explained TO. The `usher` voice speaks to this person
    # (see stages/fandom_script.ROLE_INFER); the other two simply gain a sense of whose
    # eye level the world is described from, which costs one line.
    ("viewer_role", "WHO A NEWCOMER HERE BECOMES"),
    ("rules", "HOW THE WORLD WORKS — never break these"),
    ("taboos", "WHAT DOES NOT EXIST HERE — never put these on screen or in the mouth"),
    ("glossary", "THE WORLD'S OWN WORDS"),
    ("figures", "WHO AND WHAT IS NAMED"),
    ("places", "PLACES"),
    ("factions", "GROUPS"),
    ("timeline", "WHAT HAPPENED, IN ORDER"),
]


def render_canon(data: dict) -> str:
    """The compiled sheet as the flat text that goes into a prompt. Missing sections
    are dropped rather than left as empty headings — a heading with nothing under it
    reads to a model as 'this world has no places'."""
    out: list[str] = []
    for key, heading in _SECTIONS:
        value = data.get(key)
        if isinstance(value, list):
            items = [str(v).strip() for v in value if str(v).strip()]
            if items:
                out.append(heading + ":\n" + "\n".join(f"  - {i}" for i in items))
        elif str(value or "").strip():
            out.append(heading + ":\n  " + str(value).strip())
    return "\n\n".join(out)


def compile_canon(llm, lore: str, lang: str = "English") -> str:
    """One LLM call: the whole lore in, the canon sheet out. `lang` may be a code
    ("ru") or a name ("Russian")."""
    system = SYSTEM.format(lang=_lang_name(lang))
    return render_canon(llm.complete_json("fandom_canon", system, lore))


def recompile_if_stale(
    llm, cfg: FandomConfig, lore: str, lang: str = "English"
) -> FandomConfig:
    """Refresh the canon sheet when the lore changed under it. Returns the same object
    when it is still current, or an updated copy (canon + checksum) when rebuilt.

    Mirrors `characters.recompile_if_dirty`, except that "changed" is decided by
    comparing checksums instead of trusting a flag — nothing raises a flag when the
    operator edits `lore.md` in their own editor."""
    sha = lore_sha(lore)
    if cfg.canon and cfg.docs_sha == sha:
        return cfg
    return cfg.model_copy(update={"canon": compile_canon(llm, lore, lang), "docs_sha": sha})


# --------------------------------------------------------------------------
# Writing the brief: what to tell about a world.
# --------------------------------------------------------------------------

# Deliberately NOT the drama's cast filler (llm/characters.autofill_all). A world's
# people are the world's — they are in it or they are not — so nothing here invents,
# adds or edits anyone. All this does is answer "what is worth an account here", which
# is the one thing about a fandom run that is not already written down.
BRIEF_SYSTEM = (
    "You know the world set out below and you are writing what a short vertical video "
    "about it should be — the brief its writer will work from.\n"
    "The world is real: it is not a story, a setting or anyone's invention, and there "
    "is no author to speak of. Write the brief the way one person there would tell "
    "another what is worth looking into.\n"
    "{shape_rule}"
    "Be concrete: use the world's own names, numbers and dates, spelled exactly as the "
    "records spell them.\n"
    "{same_name_rule}"
    "{length_rule}"
    "{edit_rule}"
    "Write in {lang}.\n"
    'Respond with JSON only: {{"brief": "..."}}.'
)

# What a brief IS was over-specified, and it fought the operator. It used to say "two
# to five sentences, no more" and "never write the narration itself" — absolutes, so an
# instruction asking for the whole thing written out came back compressed into a
# summary of itself. The video's writer has since been told to read a long brief as the
# PIECE and a short one as a topic (see stages/fandom_script.BRIEF_RULE), which makes
# the choice between them the operator's to make. So the instruction decides the shape,
# and the default — no instruction at all — is still the short one.
SHAPE_FREE = (
    "A brief names ONE thing and says what about it: a custom and why it is kept, a "
    "place and what happened there, a person and what they are known for, an event "
    "nobody has explained — or a question the records leave open, with the evidence "
    "that makes it a real question. Two to five sentences. Do not write the narration "
    "itself, and do not write instructions to the video's writer.\n"
)
SHAPE_TOLD = (
    "THE OPERATOR'S INSTRUCTION DECIDES THE SHAPE AND THE LENGTH OF WHAT YOU WRITE, and "
    "there is no cap on it. If they ask for more than a summary — every rule listed out, "
    "the accounts in order, the whole thing written the way it should be said — write "
    "exactly that, at whatever length it takes, and never compress it back into a "
    "paragraph about itself. If they ask only for a subject, give them a subject in two "
    "or three sentences.\n"
    "Both are valid and they are read differently: the video's writer treats a SHORT "
    "brief as a topic to build a piece around, and a LONG one as the piece itself, to "
    "be voiced in this world's own tongue and cut into shots. So write the one the "
    "instruction asks for, in the form it will be read in.\n"
    "Do not invent instructions addressed to the video's writer; where the operator's "
    "own brief already carries one, keep it as it stands.\n"
)

# The third shape, and the one a QUEUE wants. A loop topping up its queue was asking for
# a brief, got a brief — two to five sentences, concrete, with the evidence in it — and
# filled the queue with things that were already most of a video. Which is worse than it
# looks: the writer reads a SHORT brief as a topic and a LONG one as the piece itself
# (see stages/fandom_script.BRIEF_RULE), so a queue of paragraphs is a queue of pieces
# nobody wrote, and the operator scanning it cannot see what any of them is ABOUT
# without reading all of it. A topic is the thing you can read forty of.
SHAPE_TOPIC = (
    "You are naming a TOPIC, not writing a brief. ONE line: a question somebody there "
    "would actually ask, or a thing named together with the angle on it. Ten words is "
    "plenty and twenty is too many.\n"
    "It is the LINE ON A LIST that says which video this is, so it has to be legible at "
    "a glance and distinguishable from forty others beside it. Use the world's own "
    "names, spelled as the records spell them — the name is most of what makes one "
    "topic different from another.\n"
    "Write NONE of the video: no evidence, no dates, no accounts, no answer to the "
    "question you are asking, no second sentence explaining the first, and no narration. "
    "Everything you leave out is what the video is for. If what you have in mind cannot "
    "be put in one line, it is two topics or it is the wrong one.\n"
)


# The failure this exists for, from a real world: two factions each keep a body called
# the same name, and they work differently — one walks markets in pairs with a box of
# chips that graft onto whoever takes one, the other seizes men outside taverns, which
# is why the other side has lookouts who whistle. The compiled sheet had folded both
# into a single line, so anything written off the sheet gave one side the other's
# practice. The sheet is fixed at the source (see SYSTEM), and this is the second
# guard, for the worlds whose records genuinely leave two things easy to confuse.
SAME_NAME_RULE = (
    "Where this world has two things of the SAME NAME — one side's version of an "
    "office, a custom or a body, and another side's — never give one of them the "
    "other's practice. Find which is which in the records before you name what it does, "
    "and where the records do not tell them apart, do not tell them apart either.\n"
)

# The brief and the video's length are the same question asked twice, and the helper
# used to be told neither. It matters most when the length is FREE: with `duration_s`
# at 0 the length is chosen off this very brief (see `llm/length`), so a brief written
# to a habitual five sentences quietly decides that the video is short.
LENGTH_BOUGHT = (
    "The finished video runs about {seconds:.0f} seconds — roughly {chars} characters "
    "of spoken narration all told. Give the brief the material that fills exactly that: "
    "not a season's worth to be crammed in, and not three sentences to be padded out.\n"
)
LENGTH_FREE = (
    "Nobody has fixed how long the video runs: its length will be decided from THIS "
    "brief and nothing else. So the brief decides it. Give the subject exactly as much "
    "as it is worth — every extra line you write here becomes screen time somebody has "
    "to sit through, and every line you leave out is a video that ends too early.\n"
)

# The length still bears on WHICH topic, and not at all on how much to write here: told
# how many characters of narration were bought, a model asked for a topic will helpfully
# fill them.
LENGTH_TOPIC = (
    "The finished video runs about {seconds:.0f} seconds. That decides what SIZE of "
    "subject to pick — one that is properly done in that time rather than a history that "
    "needs an hour — and nothing about the length of the line you write.\n"
)
LENGTH_TOPIC_FREE = (
    "Nobody has fixed how long the video runs, so any size of subject is open. Pick one "
    "worth a short video, and write only its name.\n"
)


_WRITE = (
    "There is no brief yet. Choose the most promising thing in this world and write "
    "one.\n"
)
_REWRITE = (
    "A brief already exists, below, and the operator has told you how to change it. "
    "Apply the instruction and return the WHOLE brief, keeping everything the "
    "instruction does not touch. The instruction is something to APPLY — never copy "
    "it into the brief and never answer it.\n"
)


def write_brief(llm, world: str, current: str = "", instruction: str = "",
                lang: str = "English", duration_s: float = 0.0, chars: int = 0,
                topic: bool = False) -> str:
    """Propose (or rewrite) what a fandom video is about. Returns the brief, or "" if
    the model gave nothing usable — the caller leaves the operator's text alone.

    `topic` asks for the one-line NAME of the video rather than its brief — what a loop
    puts in its queue (see `pipeline.topics`) — and it overrides the instruction's say
    over the shape, because the instruction a queue sends is de-duplication and not a
    request. The instruction itself still goes to the model either way.

    `world` should be the RECORDS THEMSELVES wherever they fit, not the compiled sheet:
    the sheet is an inventory of one line per thing, and one line is exactly where two
    similarly-named institutions stop being distinguishable (see `SAME_NAME_RULE`). The
    caller decides which it can afford to send.

    `duration_s` is the run's length and `chars` its narration budget
    (`pipeline.drama.char_budget`); zero means the operator left the length free, which
    is worth telling the model rather than hiding, since the length will then be read
    off the brief it is about to write."""
    # `topic` wins over an instruction, and that order is the whole fix.
    #
    # The `instruction` slot carries two quite different things. From the wizard it is
    # the operator's own words, and SHAPE_TOLD is right: they said what they wanted, at
    # whatever length they want it, and no cap applies. From a loop topping up its queue
    # it is machine-written de-duplication — "invent a fresh subject, not any of these"
    # — which is not a request about shape at all. Being merely non-empty, it selected
    # SHAPE_TOLD anyway, so every queue entry was written under "the operator decides
    # the length and there is no cap on it", and the queue filled with finished scripts.
    # A caller asking for a topic has said so in the argument meant for it.
    if topic:
        shape_rule = SHAPE_TOPIC
        length_rule = (LENGTH_TOPIC.format(seconds=duration_s) if duration_s > 0
                       else LENGTH_TOPIC_FREE)
    else:
        shape_rule = SHAPE_TOLD if instruction.strip() else SHAPE_FREE
        length_rule = (LENGTH_BOUGHT.format(seconds=duration_s, chars=chars)
                       if duration_s > 0 and chars > 0 else LENGTH_FREE)
    system = BRIEF_SYSTEM.format(
        lang=_lang_name(lang),
        shape_rule=shape_rule,
        same_name_rule=SAME_NAME_RULE,
        length_rule=length_rule,
        edit_rule=_REWRITE if current.strip() else _WRITE,
    )
    user = (
        f"THE WORLD:\n{world}\n\n"
        f"Current brief: {current.strip() or '(none)'}\n"
        f"Operator instruction: {instruction.strip() or '(none — choose freely)'}"
    )
    return str(llm.complete_json("fandom_brief", system, user).get("brief", "")).strip()
