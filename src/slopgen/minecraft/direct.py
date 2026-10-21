"""The director: a model writes the first direction of every machinima block of a video.

It cannot see the world, so it is told it in words: each place with its zones,
points, lines and cameras — their roles and the operator's own descriptions — the
characters' faces and bodies, the props, the gestures and poses the bodies know, the
crowd behaviours and the kinds of camera. And it is told the narration of each block
WORD BY WORD with the second each word is spoken, because every key it writes is
anchored to one of those words.

One call for all the blocks of a video, then one repair round: whatever the
validator finds wrong goes back with the model's own answer, and a block still wrong
after that is given up — its stretches go back to the other picture sources.
"""

from __future__ import annotations

import json
import logging

from ..pipeline.job import MachinimaBlock, VideoJob
from .anchors import Clock, ClockWord
from .direction import BEHAVIOURS, SIDES, TIMES, VERBS, WEATHERS, Direction, WordRef, validate
from .library import Library
from .registry import Area, Camera, Line, Point, Registry

log = logging.getLogger(__name__)

SYSTEM = (
    "You are the director of short machinima pieces: moments of a narrated video that are "
    "PLAYED by actors in a Minecraft build of the story's world and filmed under the very words "
    "being spoken. The world is real to its people — stage it as life there, never as a game.\n"
    "YOU GET, for each block: the words heard over it, numbered, each with the second it is "
    "spoken, and what the picture editor wanted shown. And the build: its places, and in each "
    "its marked zones, points, lines and cameras with their roles and descriptions; the "
    "characters with their faces (a face is written \"Character/face\") and bodies; the props; "
    "the gestures and poses; the crowd behaviours.\n"
    "YOU WRITE a direction per block: one or more scenes (one place each; a new scene is a cut to "
    "another place), each with its cast, crowds, actions and shots.\n"
    "ANCHORS. Every key says WHEN with an anchor on the words: {{\"w\": N}} is the moment word N "
    "begins, {{\"w\": N, \"offset\": -0.3}} a little before it, {{\"between\": [N, M], \"frac\": 0.5}} "
    "halfway from word N to word M. Use {{\"sec\": S}} (seconds from the block's start) only for what "
    "no word speaks to.\n"
    "STAGING RULES:\n"
    "  • The action lands ON the word it is about: a nod on «кивнула», a turn on «обернулся».\n"
    "  • The camera is on whoever is being talked about. A conversation is a `dialogue` action and "
    "a `style: dialogue` shot.\n"
    "  • One thought per shot; cut when the narration moves on. A shot lasts at least ~1.5 s.\n"
    "  • Nobody stands idle for long: somebody not acting is listening, looking, or doing their "
    "work (a gesture that loops).\n"
    "  • Use only names that exist in the place you chose — points, zones, lines and cameras are "
    "per place. Main actors name a FACE. Stand people on points or in zones.\n"
    "  • A crowd is configured, not listed: a zone, a density 0..1, and the mix of characters "
    "(\"Character\" for all its faces, or \"Character/face\") with shares.\n"
    "  • Keep it playable: up to about six main actors; nothing the bodies cannot do — no fine hand "
    "work, no faces, no lip sync.\n"
    "GRAMMAR (JSON):\n"
    "scene = {{\"at\": anchor (not for the first scene), \"place\": name, \"time\": {times} or ticks, "
    "\"weather\": {weathers}, \"cast\": [cast], \"crowd\": [crowd], \"action\": [action], \"shots\": [shot]}}\n"
    "cast = {{\"as\": short alias, \"actor\": \"Character/face\", \"at\": point or zone, "
    "\"facing\": target, \"hold\": prop}}\n"
    "crowd = {{\"in\": zone, \"density\": 0..1, \"mix\": {{ref: share}}, \"exclude\": [ref], "
    "\"do\": {behaviours}, \"along\": line, \"toward\": target}}\n"
    "action = {{\"at\": anchor, \"do\": verb, \"who\": alias or [aliases], ...}} — verbs and what they "
    "need: {verbs}. `to` and `on` are targets (an alias, a point or a zone); `until` is an anchor "
    "that ends a look or a looping gesture; `ease` is step|linear|smooth|in|out|overshoot. A "
    "dialogue has `turns`: [{{\"speaker\": alias, \"until\": anchor}}].\n"
    "shot = {{\"at\": anchor, \"camera\": camera}} where camera is ONE of:\n"
    "  {{\"on\": alias, \"side\": {sides}, \"distance\": blocks or [from, to], \"height\": blocks above "
    "the eyes, \"orbit\": degrees (side orbit)}} — rides a character; the camera keeps them in "
    "view on its own;\n"
    "  {{\"style\": \"dialogue\", \"pair\": [alias, alias], \"distance\": blocks}} — over the shoulder, "
    "cutting to whoever speaks;\n"
    "  {{\"fixed\": camera name, \"aim\": target, \"fov\": degrees or [from, to]}} — a marked camera;\n"
    "  {{\"rail\": camera name, \"travel\": [from, to] fractions of the rail, \"aim\": target}}.\n"
    "Distances: 1–1.5 a face, 2–3 a person, 4–6 a group, 8+ the place.\n"
    "Respond with JSON only: {{\"blocks\": {{\"<block id>\": {{\"scenes\": [scene, ...]}}, ...}}}}."
)


def _registry_block(reg: Registry, lib: Library) -> str:
    out = []
    for p in reg.places:
        out.append(f"PLACE «{p.name}»" + (f": {p.description}" if p.description else ""))
        for e in reg.elements(p):
            kind = ("zone" if isinstance(e, Area) else "point" if isinstance(e, Point)
                    else "line" if isinstance(e, Line) else f"camera ({e.kind})" if isinstance(e, Camera) else "?")
            roles = ", ".join(getattr(e, "roles", []) or [])
            out.append(f"  - {kind} «{e.name}»" + (f" [{roles}]" if roles else "")
                       + (f": {e.description}" if e.description else ""))
    out.append("CHARACTERS (faces):")
    for c in reg.characters:
        faces = "; ".join(f"{c.name}/{v.name} ({v.body})" + (f" — {v.description}" if v.description else "")
                          for v in c.variants)
        out.append(f"  - {faces}")
    if reg.props:
        out.append("PROPS: " + "; ".join(f"«{p.name}»" + (f" ({p.description})" if p.description else "")
                                          for p in reg.props))
    out.append("POSES (held): " + "; ".join(f"{n} — {p.description}" for n, p in lib.poses.items()))
    out.append("GESTURES (played): " + "; ".join(
        f"{n}{' (loops)' if g.loop else ''} — {g.description}" for n, g in lib.gestures.items()))
    return "\n".join(out)


def _words_block(ws: list[ClockWord], start: float) -> str:
    return " ".join(f"[{k}|{w.start - start:.1f}s] {w.text}" for k, w in enumerate(ws))


def _system() -> str:
    return SYSTEM.format(
        times="|".join(TIMES), weathers="|".join(WEATHERS), behaviours="|".join(BEHAVIOURS),
        sides="|".join(SIDES),
        verbs="; ".join(f"{v}({', '.join(need) or '—'})" for v, need in VERBS.items()))


def direct_blocks(job: VideoJob, blocks: list[MachinimaBlock], reg: Registry, lib: Library,
                  clock: Clock, ctx) -> None:
    """Write a direction file for every block given, or give the block up."""
    from .block import give_up, save_direction

    spans = {b.id: clock.span(b.start, b.start + b.duration) for b in blocks}
    user = _registry_block(reg, lib) + "\n\nBLOCKS:\n" + "\n\n".join(
        f"BLOCK {b.id} — {b.duration:.1f}s" + (f", the editor suggests «{b.place}»" if b.place else "")
        + (f"\n  to show: {b.why}" if b.why else "")
        + f"\n  words: {_words_block(spans[b.id], b.start)}"
        for b in blocks)
    data = ctx.llm.complete_json("machinima_direct", _system(), user)
    pending = {b.id: b for b in blocks}
    errors: dict[str, list[str]] = {}
    parsed: dict[str, Direction] = {}
    for bid, b in pending.items():
        d, errs = _parse(data, bid, spans[bid], reg, lib)
        if d is not None and not errs:
            parsed[bid] = d
        else:
            errors[bid] = errs or ["no direction was written for this block"]
    if errors:
        repair = user + "\n\nYOUR ANSWER:\n" + json.dumps(data, ensure_ascii=False)[:20000] + \
            "\n\nFIX THESE, and answer with the complete corrected JSON:\n" + "\n".join(
                f"{bid}: {e}" for bid, errs in errors.items() for e in errs)
        data2 = ctx.llm.complete_json("machinima_direct", _system(), repair)
        for bid in list(errors):
            d, errs = _parse(data2, bid, spans[bid], reg, lib)
            if d is not None and not errs:
                parsed[bid] = d
                errors.pop(bid)
            else:
                errors[bid] = errs or errors[bid]
    for bid, d in parsed.items():
        save_direction(job, pending[bid], d)
    for bid, errs in errors.items():
        give_up(job, pending[bid], "the director could not stage it: " + "; ".join(errs[:5]))


def _parse(data: dict, bid: str, ws: list[ClockWord], reg: Registry, lib: Library):
    raw = (data.get("blocks") or {}).get(bid) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return None, []
    try:
        converted = _anchors(raw, ws)
        d = Direction.model_validate(converted)
    except Exception as e:
        return None, [f"not a valid direction: {e}"[:400]]
    errs = validate(d, reg, {(w.scene, w.i) for w in ws}, set(lib.gestures), set(lib.poses))
    return d, errs


def _anchors(obj, ws: list[ClockWord]):
    """The model's block-local word numbers → the narration's own word identities."""
    if isinstance(obj, list):
        return [_anchors(v, ws) for v in obj]
    if not isinstance(obj, dict):
        return obj
    if "w" in obj and isinstance(obj.get("w"), (int, float)) and set(obj) <= {"w", "offset"}:
        return {"word": _ref(int(obj["w"]), ws), "offset": float(obj.get("offset", 0.0))}
    if "between" in obj and isinstance(obj["between"], list) and obj["between"] \
            and all(isinstance(v, (int, float)) for v in obj["between"]):
        a, b = (int(v) for v in obj["between"][:2])
        return {"between": [_ref(a, ws), _ref(b, ws)], "frac": float(obj.get("frac", 0.5))}
    return {k: _anchors(v, ws) for k, v in obj.items()}


def _ref(k: int, ws: list[ClockWord]) -> dict:
    if not 0 <= k < len(ws):
        # out of range: an anchor the validator will name, rather than a silent clamp
        return WordRef(scene=-1, i=k, text=f"#{k}").model_dump()
    w = ws[k]
    return WordRef(scene=w.scene, i=w.i, text=w.text).model_dump()
