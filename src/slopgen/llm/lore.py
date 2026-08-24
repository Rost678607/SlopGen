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
    "The world is real and these are its records. Never describe it as fiction, a "
    "story, a setting or someone's work; never mention an author, a canon or an "
    "audience.\n"
    "Give:\n"
    '  • "premise": what this world is, in 2-3 sentences.\n'
    '  • "rules": how it works — the laws, limits and customs a story set here may not '
    "break. One line each.\n"
    '  • "glossary": the world\'s own terms and what each means, one line each. Every '
    "word the records use that an outsider would not know belongs here.\n"
    '  • "figures": named characters — who or WHAT they are and what they are known '
    "for. Not only people: a named beast, machine, vessel or building belongs here too "
    "if the records treat it as someone rather than something.\n"
    '  • "places": named locations, one line each.\n'
    '  • "factions": groups, guilds, families, institutions and what they want.\n'
    '  • "timeline": recorded events in order, each with whatever dating the records '
    "give.\n"
    '  • "taboos": what does NOT exist in this world and what never happens in it — '
    "absent technology, absent institutions, words nobody here would say, things "
    "outsiders would wrongly assume. Infer these from what the records show: a world of "
    "footpaths and lanterns has no cars and no telephones. This list is what stops the "
    "narrator furnishing the world out of our own.\n"
    '  • "register": how people here talk — vocabulary, formality, what they '
    "understate. One or two sentences.\n"
    "Write every field in {lang}, except proper nouns and terms, which keep the "
    "records' spelling.\n"
    'Respond with JSON only: {{"premise": "...", "rules": ["..."], "glossary": ["..."], '
    '"figures": ["..."], "places": ["..."], "factions": ["..."], "timeline": ["..."], '
    '"taboos": ["..."], "register": "..."}}.'
)

# Rendered order = reading order for the writer: what the world IS, then what binds
# it, then its nouns, then what it must never contain.
_SECTIONS = [
    ("premise", "WHAT THIS WORLD IS"),
    ("register", "HOW PEOPLE HERE TALK"),
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
    "You know the world set out below and you are proposing what a short vertical "
    "video about it should be about — the brief its writer will work from.\n"
    "The world is real: it is not a story, a setting or anyone's invention, and there "
    "is no author to speak of. Write the brief the way one person there would tell "
    "another what is worth looking into.\n"
    "A good brief names ONE thing and says what about it: a custom and why it is kept, "
    "a place and what happened there, a person and what they are known for, an event "
    "nobody has explained — or a question the records leave open, with the evidence "
    "that makes it a real question. Be concrete: use the world's own names, numbers "
    "and dates. Two to five sentences, no more. It is a brief, not a script: never "
    "write the narration itself and never address the writer.\n"
    "{edit_rule}"
    "Write in {lang}.\n"
    'Respond with JSON only: {{"brief": "..."}}.'
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
                lang: str = "English") -> str:
    """Propose (or rewrite) what a fandom video is about. Returns the brief, or "" if
    the model gave nothing usable — the caller leaves the operator's text alone."""
    system = BRIEF_SYSTEM.format(
        lang=_lang_name(lang), edit_rule=_REWRITE if current.strip() else _WRITE
    )
    user = (
        f"THE WORLD:\n{world}\n\n"
        f"Current brief: {current.strip() or '(none)'}\n"
        f"Operator instruction: {instruction.strip() or '(none — choose freely)'}"
    )
    return str(llm.complete_json("fandom_brief", system, user).get("brief", "")).strip()
