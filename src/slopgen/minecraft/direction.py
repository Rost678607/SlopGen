"""A machinima block's direction file: what happens, who does it, and where the camera is.

Block → scenes → shots. A SCENE is one place and one recorded take: its cast, its
crowds, its actions on a timeline. A SHOT is a camera inside a scene, and the cut
from one to the next is a key like any other. The file is the operator's as much as
the model's — the model writes the first draft, the operator edits it in the montage
room — so it is kept beside the video (`<video>/machinima/<block>/direction.json`)
and recompiled from, never regenerated over.

Every key is anchored to a WORD of the narration rather than to a second, so a
re-voiced line carries the nod with it (see `anchors`). Seconds survive only as the
degenerate case.

The schema is deliberately flat — an action is one record with a verb and whichever
fields that verb uses — because both a model and a form have to write it, and a
union of twenty classes is a thing neither writes well. `validate` is what holds the
verbs to their fields, and its messages are written to be read by both: they name
what was wrong and list what would have been right.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .easing import EASES, Ease
from .registry import Area, Camera, Line, Point, Registry

Side = Literal["front", "back", "left", "right", "shoulder_left", "shoulder_right", "orbit"]
SIDES: tuple[str, ...] = ("front", "back", "left", "right", "shoulder_left", "shoulder_right", "orbit")
Behaviour = Literal["stand", "wander", "queue", "flow", "sit", "watch", "talk_groups"]
BEHAVIOURS: tuple[str, ...] = ("stand", "wander", "queue", "flow", "sit", "watch", "talk_groups")
TIMES = {"dawn": 0, "morning": 1000, "day": 6000, "noon": 6000, "afternoon": 9000,
         "evening": 12000, "dusk": 12500, "night": 15000, "midnight": 18000}
WEATHERS: tuple[str, ...] = ("clear", "rain", "thunder")
HANDS: tuple[str, ...] = ("main", "off")
SLOTS: tuple[str, ...] = ("head", "chest", "legs", "feet")

# What each verb needs besides `who` and `at`. One table, read by the validator, the
# model's grammar (see `direct`) and the browser's form, so they cannot disagree.
VERBS: dict[str, tuple[str, ...]] = {
    "walk": ("to",),          # go to a point / area / actor, finding the way
    "run": ("to",),
    "turn": ("to",),          # face a target with the whole body
    "look": ("to",),          # head (and eyes) follow a target; `until` releases it
    "pose": ("name",),        # a library pose, held until the next pose
    "gesture": ("name",),     # a library gesture, played once or looped `until`
    "sit": (),                # on `on` (a seat point) or on the nearest free seat
    "stand": (),
    "hold": ("item",),        # a prop in `hand`
    "wear": ("item", "slot"),
    "use": ("on",),           # open a door, press a button — a point at the block
    "dialogue": ("turns",),   # `who` is everybody in the conversation
    "appear": (),             # step in from `on` (an entry point), or just appear
    "leave": (),              # walk out via `on` (an entry point) and vanish
    "particles": ("name",),   # smoke, dust, sparks at `at`
}


class WordRef(BaseModel):
    """A word of the narration: the scene (line) it is in and its index there — the
    same identity a picture-track cut has (`FrameShot.anchor_scene/anchor_word`).
    `text` is carried for whoever reads the file, and checked against the voice so a
    rewritten line is caught instead of silently anchoring to its neighbour."""

    scene: int
    i: int
    text: str = ""


class Anchor(BaseModel):
    """When a key happens. Exactly one of three forms:

    * `word` (+ `offset` seconds, may be negative) — on a word;
    * `between` two words + `frac` — that far through the gap from the first word's
      start to the second's;
    * `sec` — seconds from the block's start, the degenerate case, for keys nothing
      in the narration speaks to."""

    word: WordRef | None = None
    offset: float = 0.0
    between: tuple[WordRef, WordRef] | None = None
    frac: float = 0.5
    sec: float | None = None

    def form(self) -> str:
        forms = [n for n, v in (("word", self.word), ("between", self.between), ("sec", self.sec))
                 if v is not None]
        return forms[0] if len(forms) == 1 else ""


class Cast(BaseModel):
    alias: str = Field(alias="as")
    actor: str  # "Character/variant"
    at: str  # a point or an area of the scene's place
    facing: str = ""  # a target to face at the start
    hold: str = ""  # a prop in the main hand from the start
    scale: float = 1.0

    model_config = {"populate_by_name": True}


class Crowd(BaseModel):
    """Extras, configured rather than listed: how dense, and in what proportions of
    which characters and faces. `mix` keys are `Character` (all its faces), `Character/*`
    (the same) or `Character/face`; `exclude` takes the same forms. Who stands where,
    and with which face, is `crowd.assign`'s job — it keeps neighbours from wearing
    the same one."""

    area: str = Field(alias="in")
    density: float = 0.3  # 0..1 of the densest a crowd ever gets (one per two cells)
    max: int = 0  # 0 = only the global cap
    mix: dict[str, float] = {}
    exclude: list[str] = []
    do: Behaviour = "stand"
    along: str = ""  # queue / flow: the line
    toward: str = ""  # watch: what they look at; queue: what the queue is for
    seed: int = 0

    model_config = {"populate_by_name": True}


class Turn(BaseModel):
    speaker: str
    until: Anchor | None = None  # None = to the end of the dialogue / scene


class Action(BaseModel):
    at: Anchor
    do: str
    who: str | list[str] = ""
    to: str = ""
    on: str = ""
    name: str = ""
    item: str = ""
    slot: str = ""
    hand: str = "main"
    until: Anchor | None = None
    loop: bool = False
    ease: Ease = "smooth"
    turns: list[Turn] = []

    def actors(self) -> list[str]:
        return [self.who] if isinstance(self.who, str) else list(self.who)


class CameraSpec(BaseModel):
    """One of four kinds, told apart by which field is set:

    * `on` — rides a character: `side`, `distance` (blocks; two values = from → to),
      `height` above the eyes, `orbit` degrees swept when `side` is orbit;
    * `style: "dialogue"` + `pair` — over the shoulder, cutting to whoever speaks;
    * `fixed` — a marked camera, turning to `aim`;
    * `rail` — a marked rail camera travelling `travel` (from → to, fractions of
      the rail), turning to `aim`.

    `fov` zooms (two values = from → to). `ease` shapes every from → to here."""

    on: str = ""
    side: Side = "front"
    distance: float | tuple[float, float] = 3.0
    height: float = 0.0
    orbit: float = 90.0
    style: Literal["", "dialogue"] = ""
    pair: tuple[str, str] | None = None
    fixed: str = ""
    rail: str = ""
    travel: tuple[float, float] = (0.0, 1.0)
    aim: str = ""
    fov: float | tuple[float, float] = 70.0
    ease: Ease = "smooth"

    def kind(self) -> str:
        kinds = [k for k, v in (("on", self.on), ("dialogue", self.style),
                                ("fixed", self.fixed), ("rail", self.rail)) if v]
        return kinds[0] if len(kinds) == 1 else ""


class Shot(BaseModel):
    at: Anchor
    camera: CameraSpec


class Scene(BaseModel):
    at: Anchor | None = None  # where the cut to this scene lands; the first starts the block
    place: str
    time: str | int = "day"
    weather: str = "clear"
    cast: list[Cast] = []
    crowd: list[Crowd] = []
    action: list[Action] = []
    shots: list[Shot] = []


class Direction(BaseModel):
    version: int = 1
    scenes: list[Scene] = []


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate(d: Direction, reg: Registry, words: set[tuple[int, int]] | None = None,
             gestures: set[str] | None = None, poses: set[str] | None = None) -> list[str]:
    """Everything wrong with a direction, as messages a model and a person can both act
    on. Empty means it can be played.

    `words` is the set of (scene, i) the block spans; without it anchors are only
    checked for shape. `gestures` and `poses` are the library's names; without them
    any name passes — the library is the mod's, and an offline check should not
    invent one."""
    errs: list[str] = []
    if not d.scenes:
        errs.append("the block has no scenes")
    for si, sc in enumerate(d.scenes, start=1):
        where = f"scene {si}"
        place = reg.place(sc.place)
        if place is None:
            errs.append(f"{where}: no place {sc.place!r}; there are: {_names(reg.places)}")
            continue
        here = reg.elements(place)
        pts = [e for e in here if isinstance(e, Point)]
        areas = [e for e in here if isinstance(e, Area)]
        lines = [e for e in here if isinstance(e, Line)]
        cams = [e for e in here if isinstance(e, Camera)]
        aliases = [c.alias for c in sc.cast]
        if si > 1 and sc.at is None:
            errs.append(f"{where}: says nowhere where the cut to it lands (`at`)")
        if sc.at is not None:
            errs += _anchor(sc.at, f"{where} start", words)
        if not (isinstance(sc.time, int) or sc.time in TIMES):
            errs.append(f"{where}: time {sc.time!r} is not one of {', '.join(TIMES)} or a tick count")
        if sc.weather not in WEATHERS:
            errs.append(f"{where}: weather {sc.weather!r} is not one of {', '.join(WEATHERS)}")

        def target(name: str, what: str, allow: tuple[str, ...] = ("actor", "point", "area")) -> list[str]:
            if not name:
                return [f"{what}: empty target"]
            if "actor" in allow and name in aliases:
                return []
            if "point" in allow and any(p.name == name for p in pts):
                return []
            if "area" in allow and any(a.name == name for a in areas):
                return []
            if "line" in allow and any(ln.name == name for ln in lines):
                return []
            options = sorted(set(
                (aliases if "actor" in allow else []) + [p.name for p in pts if "point" in allow]
                + [a.name for a in areas if "area" in allow] + [ln.name for ln in lines if "line" in allow]))
            return [f"{what}: nothing called {name!r} in {place.name!r}; there are: {', '.join(options) or 'nothing'}"]

        seen = set()
        for ci, c in enumerate(sc.cast, start=1):
            w = f"{where}, cast {c.alias or ci}"
            if not c.alias:
                errs.append(f"{w}: no alias (`as`)")
            elif c.alias in seen:
                errs.append(f"{w}: alias {c.alias!r} used twice")
            seen.add(c.alias)
            if reg.variant(c.actor) is None:
                errs.append(f"{w}: no actor {c.actor!r} — name a face as \"Character/face\"; "
                            f"there are: {_faces(reg)}")
            errs += target(c.at, f"{w} at", ("point", "area"))
            if c.facing:
                errs += target(c.facing, f"{w} facing")
            if c.hold and reg.prop(c.hold) is None:
                errs.append(f"{w}: no prop {c.hold!r}; there are: {_names(reg.props)}")

        for ki, cr in enumerate(sc.crowd, start=1):
            w = f"{where}, crowd {ki}"
            errs += target(cr.area, f"{w} in", ("area",))
            if not 0.0 <= cr.density <= 1.0:
                errs.append(f"{w}: density {cr.density} is outside 0..1")
            if not cr.mix:
                errs.append(f"{w}: empty mix — say who the crowd is made of")
            for ref in list(cr.mix) + cr.exclude:
                name, _, face = ref.partition("/")
                ch = reg.character(name)
                if ch is None:
                    errs.append(f"{w}: no character {name!r}; there are: {_names(reg.characters)}")
                elif face and face != "*" and not any(v.name == face for v in ch.variants):
                    errs.append(f"{w}: {name!r} has no face {face!r}; it has: {_names(ch.variants)}")
            if cr.do in ("queue", "flow") and not cr.along:
                errs.append(f"{w}: a {cr.do} needs a line (`along`)")
            if cr.along:
                errs += target(cr.along, f"{w} along", ("line",))
            if cr.do == "watch" and not cr.toward:
                errs.append(f"{w}: watching needs something to watch (`toward`)")
            if cr.toward:
                errs += target(cr.toward, f"{w} toward")

        for ai, a in enumerate(sc.action, start=1):
            w = f"{where}, action {ai} ({a.do})"
            errs += _anchor(a.at, w, words)
            if a.until is not None:
                errs += _anchor(a.until, f"{w} until", words)
            if a.do not in VERBS:
                errs.append(f"{w}: unknown verb; verbs are: {', '.join(VERBS)}")
                continue
            for who in a.actors():
                if who not in aliases:
                    errs.append(f"{w}: {who!r} is not in the cast; the cast is: {', '.join(aliases) or 'nobody'}")
            for need in VERBS[a.do]:
                if not getattr(a, need):
                    errs.append(f"{w}: needs `{need}`")
            if a.ease not in EASES:
                errs.append(f"{w}: ease {a.ease!r} is not one of {', '.join(EASES)}")
            if a.do in ("walk", "run", "turn", "look") and a.to:
                errs += target(a.to, w)
            if a.do in ("sit", "use", "appear", "leave") and a.on:
                errs += target(a.on, w, ("point",))
            if a.do in ("hold", "wear") and a.item and reg.prop(a.item) is None:
                errs.append(f"{w}: no prop {a.item!r}; there are: {_names(reg.props)}")
            if a.do == "hold" and a.hand not in HANDS:
                errs.append(f"{w}: hand must be one of {', '.join(HANDS)}")
            if a.do == "wear" and a.slot and a.slot not in SLOTS:
                errs.append(f"{w}: slot must be one of {', '.join(SLOTS)}")
            if a.do == "gesture" and gestures is not None and a.name and a.name not in gestures:
                errs.append(f"{w}: no gesture {a.name!r}; there are: {', '.join(sorted(gestures))}")
            if a.do == "pose" and poses is not None and a.name and a.name not in poses:
                errs.append(f"{w}: no pose {a.name!r}; there are: {', '.join(sorted(poses))}")
            if a.do == "dialogue":
                if len(a.actors()) < 2:
                    errs.append(f"{w}: a dialogue needs at least two people in `who`")
                for ti, t in enumerate(a.turns, start=1):
                    if t.speaker not in a.actors():
                        errs.append(f"{w}, turn {ti}: speaker {t.speaker!r} is not in this dialogue")
                    if t.until is not None:
                        errs += _anchor(t.until, f"{w}, turn {ti} until", words)

        if not sc.shots:
            errs.append(f"{where}: no shots — say where the camera is")
        for hi, sh in enumerate(sc.shots, start=1):
            w = f"{where}, shot {hi}"
            errs += _anchor(sh.at, w, words)
            cam = sh.camera
            kind = cam.kind()
            if not kind:
                errs.append(f"{w}: say exactly one of `on`, `style: dialogue`, `fixed`, `rail`")
                continue
            if kind == "on" and cam.on not in aliases:
                errs.append(f"{w}: the camera rides {cam.on!r}, who is not in the cast")
            if kind == "dialogue" and (not cam.pair or any(p not in aliases for p in cam.pair)):
                errs.append(f"{w}: a dialogue shot needs `pair` — two people of the cast")
            if kind == "fixed" and not any(c.name == cam.fixed and c.kind == "fixed" for c in cams):
                errs.append(f"{w}: no fixed camera {cam.fixed!r} in {place.name!r}; there are: "
                            f"{', '.join(c.name for c in cams if c.kind == 'fixed') or 'none'}")
            if kind == "rail" and not any(c.name == cam.rail and c.kind == "rail" for c in cams):
                errs.append(f"{w}: no rail camera {cam.rail!r} in {place.name!r}; there are: "
                            f"{', '.join(c.name for c in cams if c.kind == 'rail') or 'none'}")
            if cam.aim:
                errs += target(cam.aim, f"{w} aim")
            for v in _pair(cam.distance):
                if not 0.3 <= v <= 64:
                    errs.append(f"{w}: distance {v} is outside 0.3..64 blocks")
            for v in _pair(cam.fov):
                if not 10 <= v <= 120:
                    errs.append(f"{w}: fov {v} is outside 10..120")
    return errs


def _pair(v) -> list[float]:
    return list(v) if isinstance(v, (tuple, list)) else [v]


def _anchor(a: Anchor, what: str, words: set[tuple[int, int]] | None) -> list[str]:
    form = a.form()
    if not form:
        return [f"{what}: an anchor is exactly one of `word`, `between` or `sec`"]
    if form == "between" and not 0.0 <= a.frac <= 1.0:
        return [f"{what}: frac {a.frac} is outside 0..1"]
    if words is None:
        return []
    refs = [a.word] if form == "word" else list(a.between) if form == "between" else []
    bad = [r for r in refs if (r.scene, r.i) not in words]
    return [f"{what}: word {r.text or ''!r} ({r.scene}:{r.i}) is not in this block" for r in bad]


def _names(items) -> str:
    return ", ".join(getattr(i, "name", str(i)) for i in items) or "none"


def _faces(reg: Registry) -> str:
    return ", ".join(f"{c.name}/{v.name}" for c in reg.characters for v in c.variants) or "none"
