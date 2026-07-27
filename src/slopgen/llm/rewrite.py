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


def rewrite(
    llm,
    lines: list[str],
    instruction: str,
    *,
    lang: str = "en",
    subject: str = "lines",
    variable: bool = False,
) -> list[str] | None:
    """Apply `instruction` to `lines`. Returns the new lines, or None when the model
    gave nothing usable (a fixed-length document also rejects a changed count)."""
    system = _SYSTEM.format(
        subject=subject, lang=lang, count_rule=_VARIABLE if variable else _FIXED
    )
    user = (
        f"Instruction: {instruction}\n"
        f"Lines:\n{json.dumps(lines, ensure_ascii=False, indent=1)}"
    )
    data = llm.complete_json("bp_rewrite", system, user)
    out = data.get("lines")
    if not isinstance(out, list) or not out:
        return None
    if not variable and len(out) != len(lines):
        return None
    return [str(x).strip() for x in out]
