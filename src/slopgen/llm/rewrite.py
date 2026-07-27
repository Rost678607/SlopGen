"""The AI edit line used on breakpoints: hand the LLM the lines the operator is
reviewing plus one free-form instruction ("shorter", "make scene 3 angrier", "split
this into two beats") and get the whole edited list back.

Same shape as the drama cast's AI polish (llm/characters.py): the model returns the
FULL array so the caller can swap it in wholesale, and only documents that allow it
(`variable`) may come back with a different number of lines.
"""

from __future__ import annotations

import json

_SYSTEM = (
    "You are editing part of a short vertical video on the operator's behalf: {subject}. "
    "You get the current lines as a JSON array plus one instruction. Apply the instruction "
    "and return the COMPLETE new array — every line in order, including the ones you did "
    "not touch. Never number the lines, never add stage directions or commentary: return "
    "only the content itself, in the same style and register as the input.\n"
    "{count_rule}\n"
    'Write in {lang}. Respond with JSON only: {{"lines": ["<line 1>", ...]}}.'
)

_FIXED = (
    "Keep EXACTLY the same number of lines and their order — rewrite them in place. "
    "A line you have nothing to change about must be returned unchanged."
)
_VARIABLE = (
    "Keep the order, but you MAY add, remove, split or merge lines when the instruction "
    "calls for it. Do not change the count for no reason: if the instruction is only about "
    "wording, return the same number of lines."
)


_KINDS = (
    "\nEach line comes with a `kind` saying what it is; rewrite every line in the form its "
    "kind demands and never turn one kind into another. In particular a `prompt` line is an "
    "ENGLISH visual description for an image/video generator (no character names, no cuts or "
    "'THEN' sequences — one continuous shot), while a `text` line is the spoken narration.\n"
)


def rewrite(
    llm,
    lines: list[str],
    instruction: str,
    *,
    lang: str = "en",
    subject: str = "lines",
    variable: bool = False,
    kinds: list[str] | None = None,
) -> list[str] | None:
    """Apply `instruction` to `lines`. Returns the new lines, or None when the model
    gave nothing usable (a fixed-length document also rejects a changed count).

    `kinds` labels each line for documents that mix several sorts of line (the script
    shows narration and shot prompts together); labelling pins the count, because a
    line the model invents would have no kind to belong to."""
    if kinds:
        variable = False
    system = _SYSTEM.format(
        subject=subject, lang=lang, count_rule=_VARIABLE if variable else _FIXED
    )
    if kinds:
        system += _KINDS
    payload = (
        [{"kind": k, "text": t} for k, t in zip(kinds, lines)] if kinds else lines
    )
    user = (
        f"Instruction: {instruction}\n"
        f"Lines:\n{json.dumps(payload, ensure_ascii=False, indent=1)}"
    )
    data = llm.complete_json("bp_rewrite", system, user)
    out = data.get("lines")
    if not isinstance(out, list) or not out:
        return None
    if not variable and len(out) != len(lines):
        return None
    return [str(x).strip() for x in out]


_SCENES_SYSTEM = (
    "You are editing the scene list of a short vertical video on the operator's behalf. "
    "You get every scene as an object and one instruction. Apply it and return the FULL "
    "new scene list, in the order it should play.\n"
    "You may do ANYTHING the instruction calls for: rewrite any field, REORDER scenes, "
    "merge them, split one in two, add new ones, drop existing ones.\n"
    'Each scene keeps an "id": the id of the scene it came from — carry it over even when '
    "you rewrite or move that scene, so its already-made audio and clip can be kept. Use "
    'null only for a genuinely new scene. Never invent an id that was not given to you.\n'
    "Field rules:\n"
    '  • "text" — the spoken narration, in {lang}.\n'
    '  • "prompt" — the ENGLISH shot description for a video generator. No character '
    "NAMES (it cannot map a name to a face and prints foreign names across the frame) — "
    "refer to people by a short visual tag. ONE continuous take: no cuts, no 'THEN', no "
    "montage or split screen, or the generator renders every shot at once.\n"
    '  • "cast" — who is on screen, as a list of names taken EXACTLY from this roster: '
    "{roster}. Never use a name outside it.\n"
    '  • "keywords" — English stock-search terms, as a list.\n'
    '  • "model" — the generator, one of: {models}.\n'
    '  • "clip_s" — the clip length in seconds, a number.\n'
    "Include every field the input scenes have; leave a field as it was when the "
    "instruction does not touch it.\n"
    'Respond with JSON only: {{"scenes": [{{"id": 0, ...}}, ...]}}.'
)


def rewrite_scenes(
    llm,
    scenes: list[dict],
    instruction: str,
    *,
    lang: str = "en",
    roster: list[str] | None = None,
    models: list[str] | None = None,
) -> list[dict] | None:
    """Apply a free-form instruction to a whole scene list — the structured sibling of
    :func:`rewrite`. Unlike a flat line rewrite this can reorder, add, drop and retype
    scenes, and it edits every field of one, not only its prose.

    Returns the new scene list (each entry carrying the `id` of the scene it came from,
    or None when it is new), or None when the model gave nothing usable."""
    system = _SCENES_SYSTEM.format(
        lang=lang,
        roster=", ".join(roster or []) or "(no fixed cast)",
        models=", ".join(models or []) or "(leave as is)",
    )
    user = (
        f"Instruction: {instruction}\n"
        f"Scenes:\n{json.dumps(scenes, ensure_ascii=False, indent=1)}"
    )
    data = llm.complete_json("bp_scenes", system, user)
    out = data.get("scenes")
    if not isinstance(out, list) or not out:
        return None
    known = {s.get("id") for s in scenes}
    clean: list[dict] = []
    for item in out:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        # an id the operator's list never had would silently graft one scene's audio
        # onto another's text — treat it as a new scene instead
        item["id"] = sid if sid in known and sid is not None else None
        clean.append(item)
    return clean or None
