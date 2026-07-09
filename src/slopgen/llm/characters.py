"""LLM helpers for the AI-drama cast: turn a photo into an appearance blurb,
invent missing fields, and compile the structured character into generation-ready
English prompts.

The compile step is deliberately a *prompt-engineering* pass, not a translation:
it rewrites the character into a token-dense txt2img/txt2vid descriptor (the kind
of comma-separated tag prompt diffusion models respond to best) plus a short
narrative sheet for the script. It runs lazily — only when a character's
structured fields changed since the last compile (the `dirty` flag).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from ..config.models import CharacterConfig

# fields the user fills in; the LLM compiles appearance + age into visual_prompt
STRUCT_FIELDS = ("name", "age", "appearance")


def _mime(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "image/jpeg"


def photo_to_appearance(llm, image_path: Path) -> str:
    """Vision: describe a reference photo as an English appearance blurb suitable
    for image-prompt injection (looks only — no name, no background, no mood)."""
    prompt = (
        "Describe ONLY this person's physical appearance for an image-generation "
        "prompt: hair (colour, length, style), eyes, face shape, skin, build, and "
        "clothing plus any distinctive features. English, concise, comma-separated "
        "tags. No name, no background, no personality, no sentences. Output only the tags."
    )
    return llm.describe_image(prompt, image_path.read_bytes(), _mime(image_path)).strip()


def compile_character(llm, char: dict) -> dict:
    """Compile the character into ONE generation-ready English `visual_prompt`: a
    token-dense txt2img/txt2vid tag descriptor (appearance + age folded in),
    optimized for diffusion models — not a literal translation."""
    system = (
        "You are a prompt engineer for AI image/video generation. Turn the given "
        "character into ONE English `visual_prompt`: a token-dense, comma-separated tag "
        "prompt for txt2img/txt2vid (diffusion-style). Fold in age and every visual trait; "
        "keep it concrete and reusable across scenes for a consistent face/outfit. No "
        "sentences, no name. Optimize for model comprehension — do NOT merely translate. "
        'Respond with JSON only: {"visual_prompt": "..."}.'
    )
    user = (
        f"name: {char.get('name', '')}\nage: {char.get('age', '')}\n"
        f"appearance: {char.get('appearance', '')}"
    )
    data = llm.complete_json("char_compile", system, user)
    return {"visual_prompt": str(data.get("visual_prompt", "")).strip()}


FILLABLE = ("age", "appearance")  # fields the AI may populate


def autofill_one(llm, char: dict, lang: str = "en", user_prompt: str = "") -> dict:
    """Fill/rewrite ONE character, reading only its own fields. Per-character AI
    may touch non-empty fields (unlike the whole-cast fill): a user prompt steers
    it, and with no prompt it fills empties and refines the rest. Returns just the
    fields it actually changed (for highlighting)."""
    before = {k: str(char.get(k, "")).strip() for k in FILLABLE}
    empty = [k for k, v in before.items() if not v]
    if user_prompt:
        goal = (
            "Follow this instruction from the user, rewriting ANY fields as needed "
            f"(you MAY and SHOULD overwrite non-empty fields to satisfy it): {user_prompt}"
        )
    elif empty:
        goal = (
            f"Fill these empty fields: {empty}. You may also lightly refine the other "
            "field so everything fits together, keeping its core meaning."
        )
    else:
        goal = (
            "All fields are already filled. REWRITE each of age and appearance into a "
            "richer, more specific and vivid version (fresh wording, add concrete detail) "
            "while keeping the character's identity. You MUST return new, changed values "
            "for BOTH fields — do not return them unchanged."
        )
    system = (
        "You design one character for a short dramatic video (anime web drama). "
        f"`appearance` = looks/clothing/build. {goal}\n"
        f"Write values in {lang}. Respond with JSON only: an object holding ONLY the keys "
        "you changed (age/appearance), each a non-empty string."
    )
    user = f"Character: {{'name': {char.get('name','')!r}, {before}}}"
    data = llm.complete_json("char_autofill", system, user)
    out = {}
    for k in FILLABLE:
        v = str(data.get(k, "")).strip()
        if v and v != before[k]:  # accept any genuine change (empty or rewritten)
            out[k] = v
    return out


def autofill_all(
    llm, cast: list[dict], lang: str = "en", scenario: str = "", user_prompt: str = ""
) -> dict:
    """Fill the whole cast at once, reading every character plus the scenario for a
    coherent ensemble. Without a prompt it only fills EMPTY character fields; WITH a
    prompt it may also rewrite non-empty fields to satisfy the instruction. The plot
    (`scenario`) is only rewritten when the prompt asks. Returns
    {"cast": [per-character changed dict aligned with cast],
    "scenario": <new plot> (only if changed)}."""
    roster = [
        {"name": c.get("name", f"#{i}"), **{k: str(c.get(k, "")).strip() for k in FILLABLE}}
        for i, c in enumerate(cast)
    ]
    all_full = all(all(m[k] for k in FILLABLE) for m in roster)
    if all_full and not user_prompt:
        return {"cast": [{} for _ in cast]}
    if user_prompt:
        edit_rule = (
            "Follow the user instruction. You MAY overwrite non-empty character fields "
            "when the instruction calls for it, and fill any empty ones. "
            "It MAY also ask to write/change the plot — if so return an updated "
            '"scenario"; otherwise omit "scenario". '
        )
    else:
        edit_rule = (
            "Fill in the EMPTY fields of each character so the ensemble is coherent "
            '(relationships, contrasts, fitting the premise). NEVER change filled fields. '
            'Do NOT return "scenario". '
        )
    system = (
        "You are casting a short dramatic anime-style web drama. `appearance` = "
        "looks/clothing/build. Improvise freely where the premise or characters are thin. "
        f"{edit_rule}Write values in {lang}.\n"
        'Respond with JSON only: {"cast": [{"age": "...", "appearance": "..."}, ...], '
        '"scenario": "..."} — cast same order/length as input, each object holding ONLY '
        "the fields you changed."
    )
    user = (
        f"Premise/scenario: {scenario or '(none given)'}\n"
        f"User instruction: {user_prompt or '(none)'}\nCast: {roster}"
    )
    data = llm.complete_json("char_autofill", system, user)
    rows = data.get("cast") if isinstance(data.get("cast"), list) else []
    cast_changes = []
    for i, member in enumerate(roster):
        changed = {}
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        for k in FILLABLE:
            v = str(row.get(k, "")).strip()
            # no prompt: only fill empties; with a prompt: accept any genuine change
            if v and (v != member[k] if user_prompt else not member[k]):
                changed[k] = v
        cast_changes.append(changed)
    out: dict = {"cast": cast_changes}
    new_plot = str(data.get("scenario", "")).strip()
    if user_prompt and new_plot and new_plot != scenario.strip():
        out["scenario"] = new_plot
    return out


def recompile_if_dirty(llm, char: CharacterConfig) -> CharacterConfig:
    """Lazily refresh the compiled prompts when the character changed. Returns the
    same object when clean, or an updated copy (dirty cleared) when recompiled."""
    if not char.dirty and char.visual_prompt:
        return char
    out = compile_character(llm, char.model_dump())
    return char.model_copy(update={**out, "dirty": False})
