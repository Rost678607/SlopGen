"""A direction, compiled into what the `slopcam` mod plays: one script per scene.

The direction speaks in words and names — «b walks to the till on "подошёл"». The
mod needs ticks and coordinates, and nothing else: it should not have to know what a
dialogue is, what a crowd is, or how a nod is shaped. So everything with meaning is
decided here, where it can be tested, and the script is a flat list of timed events
per actor plus, for bodies, dense keyed tracks per part.

Three expansions do most of the work:

* POSES AND GESTURES are composed into one track per body part — the pose held,
  with any gesture added on top (a nod while the head is bowed is a nod from bowed),
  sampled densely enough that the mod only interpolates linearly between samples and
  every curve the operator chose survives (see `library`, `easing`).
* A DIALOGUE becomes turns: the speaker talks, the others look at the speaker,
  listen and now and then nod — seeded, so a redraft does not reshuffle the nods.
* A CROWD becomes extras (`crowd.assign`) with a behaviour each: idle glances,
  wandering, chatting circles, a flow along a route.

A scene is played with a PREROLL before its first second, so spawning and the first
pathfinding step are never on camera; the export window starts after it.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from . import crowd as crowds
from .anchors import Clock, resolve
from .direction import TIMES, Action, Direction, Scene
from .easing import sample
from .library import Library, Pose, gesture_keys, pose_keys
from .registry import Area, Line, Point, Registry
from .voxels import World

TPS = 20
PREROLL = 1.5  # seconds played before the scene's first second
TRACK_RATE = 10  # samples per second of a composed body-part track
WALK_SPEED = 1.0  # Easy NPC navigation speed multipliers
RUN_SPEED = 1.7


def tick(sec: float) -> int:
    return int(round(sec * TPS))


@dataclass
class ScenePlan:
    """One scene, compiled: the script for the mod and what the camera stage needs
    later — when the scene sits in the block, and which shots it holds."""

    index: int
    start: float  # block seconds
    end: float
    script: dict
    shots: list[dict] = field(default_factory=list)  # {"start","end","camera"} in scene seconds
    warnings: list[str] = field(default_factory=list)


def scene_spans(d: Direction, clock: Clock, block_start: float, block_len: float) -> list[tuple[float, float]]:
    starts = [0.0 if i == 0 or sc.at is None else resolve(sc.at, clock, block_start)
              for i, sc in enumerate(d.scenes)]
    ends = starts[1:] + [block_len]
    return list(zip(starts, ends))


def compile_block(d: Direction, reg: Registry, lib: Library, clock: Clock, block_start: float,
                  block_len: float, worlds: dict[str, World] | None = None, cap: int = crowds.CAP) -> list[ScenePlan]:
    """Every scene of a block. `worlds` maps a place name to its scanned world, which
    crowds need to find where people can stand; without one a crowd is skipped with
    a warning rather than stood inside walls."""
    out = []
    for i, ((s0, s1), sc) in enumerate(zip(scene_spans(d, clock, block_start, block_len), d.scenes)):
        world = (worlds or {}).get(sc.place)
        out.append(_scene(i, sc, reg, lib, clock, block_start, s0, s1, world, cap))
    return out


# --------------------------------------------------------------------------


class _Ctx:
    def __init__(self, sc: Scene, reg: Registry, clock: Clock, block_start: float, s0: float):
        self.sc, self.reg, self.clock = sc, reg, clock
        self.block_start, self.s0 = block_start, s0
        self.place = reg.place(sc.place)
        here = reg.elements(self.place) if self.place else []
        self.points = {e.name: e for e in here if isinstance(e, Point)}
        self.areas = {e.name: e for e in here if isinstance(e, Area)}
        self.lines = {e.name: e for e in here if isinstance(e, Line)}
        self.aliases = {c.alias for c in sc.cast}
        self.warnings: list[str] = []

    def t(self, anchor) -> float:
        """Take seconds (preroll included) for an anchor."""
        return resolve(anchor, self.clock, self.block_start) - self.s0 + PREROLL

    def target(self, name: str) -> dict:
        if name in self.aliases:
            return {"actor": name}
        if name in self.points:
            return {"pos": list(self.points[name].pos)}
        if name in self.areas:
            return {"pos": list(_area_centre(self.areas[name]))}
        if name in self.lines and self.lines[name].points:
            return {"pos": list(self.lines[name].points[0])}
        self.warnings.append(f"nothing called {name!r} here")
        return {}

    def spot(self, name: str, taken: set) -> tuple[tuple[float, float, float], float]:
        """Where somebody told to be `at` a point or an area stands, and facing where."""
        if name in self.points:
            p = self.points[name]
            return p.pos, p.yaw
        if name in self.areas:
            cells = [c for c in self.areas[name].cells() if c not in taken]
            if cells:
                mid = _area_centre(self.areas[name])
                c = min(cells, key=lambda c: math.dist(crowds.centre(c), mid))
                taken.add(c)
                return crowds.centre(c), 0.0
        self.warnings.append(f"cannot stand anybody at {name!r}")
        return (0.0, 0.0, 0.0), 0.0


def _area_centre(a: Area) -> tuple[float, float, float]:
    cells = a.cells()
    if not cells:
        return (0.0, 0.0, 0.0)
    return tuple(sum(c[i] for c in cells) / len(cells) + (0.5 if i != 1 else 0.0) for i in range(3))  # type: ignore


class _Body:
    """The layers of one actor's body over the take: poses and gestures."""

    def __init__(self):
        self.pose_keys: dict[str, list[tuple[float, tuple, str]]] = {}
        self.gestures: list[dict[str, list[tuple[float, tuple, str]]]] = []
        self.offsets: list[tuple[float, tuple[float, float, float]]] = []
        self.vanilla: list[tuple[float, str]] = []

    def pose(self, p: Pose, at: float, ease: str):
        for part, keys in pose_keys(p, at, ease).items():
            track = self.pose_keys.setdefault(part, [(0.0, (0.0, 0.0, 0.0), "step")])
            track.sort(key=lambda k: k[0])
            # hold where the part IS at `at`, so the move to the new pose starts now
            # and not back at whenever the previous key was
            here = tuple(sample([(kt, kr[a], ke) for kt, kr, ke in track], at) for a in range(3))
            track.append((at, here, "step"))
            track.extend(keys)
        self.offsets.append((at + p.settle, p.offset))
        self.vanilla.append((at, p.vanilla or "STANDING"))

    def tracks(self, length: float) -> dict[str, list[list]]:
        """Each touched part as [tick, [x, y, z]] samples of pose + gestures.

        Sampled densely, then thinned to the two ends of every stretch where nothing
        changes — so linear interpolation between what is kept reproduces the dense
        track exactly, and a held pose costs two samples instead of a hundred."""
        parts = set(self.pose_keys) | {p for g in self.gestures for p in g}
        out = {}
        n = int(length * TRACK_RATE) + 1
        for part in sorted(parts):
            pk = sorted(self.pose_keys.get(part, [(0.0, (0.0, 0.0, 0.0), "step")]), key=lambda k: k[0])
            gl = [g[part] for g in self.gestures if part in g]
            dense = []
            for k in range(n):
                t = k / TRACK_RATE
                rot = [sample([(kt, kr[a], ke) for kt, kr, ke in pk], t) for a in range(3)]
                for keys in gl:
                    if keys and keys[0][0] <= t <= keys[-1][0]:
                        for a in range(3):
                            rot[a] += sample([(kt, kr[a], ke) for kt, kr, ke in keys], t)
                dense.append((tick(t), [round(v, 2) for v in rot]))
            kept = [d for i, d in enumerate(dense)
                    if i == 0 or i == len(dense) - 1
                    or d[1] != dense[i - 1][1] or d[1] != dense[i + 1][1]]
            out[part] = [[tk, rot] for tk, rot in kept]
        return out


def _scene(i: int, sc: Scene, reg: Registry, lib: Library, clock: Clock, block_start: float,
           s0: float, s1: float, world: World | None, cap: int) -> ScenePlan:
    cx = _Ctx(sc, reg, clock, block_start, s0)
    length = (s1 - s0) + PREROLL
    rng = random.Random(f"scene|{i}|{sc.place}")
    actors: list[dict] = []
    events: list[dict] = []
    bodies: dict[str, _Body] = {}
    taken: set = set()
    stand = lib.poses.get("stand", Pose())

    # -- the cast --------------------------------------------------------------
    appear = {a.actors()[0]: a for a in sc.action if a.do == "appear" and a.actors()}
    for c in sc.cast:
        pos, yaw = cx.spot(c.at, taken)
        found = reg.variant(c.actor)
        ch, var = found if found else (None, None)
        actor = {"id": c.alias, "face": c.actor, "preset": var.preset if var else "",
                 "body": var.body if var else "humanoid", "pos": list(pos), "yaw": yaw,
                 "scale": c.scale, "equip": {}, "hidden": c.alias in appear}
        if c.hold:
            p = reg.prop(c.hold)
            actor["equip"]["mainhand"] = p.item if p else ""
        actors.append(actor)
        b = bodies.setdefault(c.alias, _Body())
        b.pose(stand, 0.0, "step")
        if c.facing:
            events.append({"tick": 0, "actor": c.alias, "op": "turn", "target": cx.target(c.facing)})

    # -- actions ---------------------------------------------------------------
    for a in sc.action:
        try:
            t = cx.t(a.at)
            until = cx.t(a.until) if a.until else None
        except Exception as e:  # an anchor the voice no longer has
            cx.warnings.append(f"{a.do}: {e}")
            continue
        events += _action(a, t, until, cx, bodies, lib, length, rng)

    # -- crowds ----------------------------------------------------------------
    extras_n = 0
    for k, cr in enumerate(sc.crowd):
        area = cx.areas.get(cr.area)
        if area is None or world is None:
            cx.warnings.append(f"crowd {k + 1}: no world scan for {sc.place!r}, crowd left out")
            continue
        forbidden = [a for a in cx.areas.values() if "forbidden" in a.roles]
        cells = [c for c in area.cells() if not any(f.contains(c) for f in forbidden)]
        seats = [p for p in cx.points.values() if "seat" in p.roles and
                 (area.contains(_cell(p.pos)) or not area.cells())]
        line = cx.lines.get(cr.along)
        toward = cx.target(cr.toward).get("pos") if cr.toward else None
        if toward is None and cr.toward in cx.aliases:
            toward = next(a["pos"] for a in actors if a["id"] == cr.toward)
        people = crowds.assign(cr, reg, world, cells, seats=seats, line=line, toward=toward,
                               taken=taken, cap=max(0, cap - extras_n))
        extras_n += len(people)
        actors_e, events_e = _crowd(k, cr, people, cells, world, cx, bodies, lib, length, line)
        actors += actors_e
        events += events_e

    # -- shots, for the live draft camera and for the camera stage later --------
    shots = []
    for j, sh in enumerate(sc.shots):
        try:
            st = cx.t(sh.at) - PREROLL
        except Exception as e:
            cx.warnings.append(f"shot {j + 1}: {e}")
            continue
        shots.append({"start": max(0.0, st), "camera": sh.camera.model_dump(mode="json")})
    shots.sort(key=lambda s: s["start"])
    for j, s in enumerate(shots):
        s["end"] = shots[j + 1]["start"] if j + 1 < len(shots) else s1 - s0

    script = {
        "scene": i,
        "place": sc.place,
        "time": TIMES.get(sc.time, 6000) if isinstance(sc.time, str) else int(sc.time),
        "weather": sc.weather,
        "preroll": tick(PREROLL),
        "ticks": tick(length),
        "actors": actors,
        "events": sorted(events, key=lambda e: (e["tick"], e["actor"])),
        "bodies": {alias: {"parts": b.tracks(length),
                           "offset": [[tick(t), list(o)] for t, o in b.offsets],
                           "vanilla": [[tick(t), v] for t, v in b.vanilla]}
                   for alias, b in bodies.items()},
        "shots": [{**s, "start_tick": tick(s["start"] + PREROLL), "end_tick": tick(s["end"] + PREROLL)}
                  for s in shots],
    }
    return ScenePlan(i, s0, s1, script, shots, cx.warnings)


def _cell(p) -> tuple[int, int, int]:
    return (math.floor(p[0]), math.floor(p[1]), math.floor(p[2]))


def _action(a: Action, t: float, until: float | None, cx: _Ctx, bodies: dict[str, _Body],
            lib: Library, length: float, rng: random.Random) -> list[dict]:
    ev: list[dict] = []
    who = a.actors()
    tk = tick(t)
    for alias in who if a.do != "dialogue" else []:
        b = bodies.setdefault(alias, _Body())
        if a.do in ("walk", "run"):
            ev.append({"tick": tk, "actor": alias, "op": "navigate", "target": cx.target(a.to),
                       "speed": RUN_SPEED if a.do == "run" else WALK_SPEED})
        elif a.do == "turn":
            ev.append({"tick": tk, "actor": alias, "op": "turn", "target": cx.target(a.to)})
        elif a.do == "look":
            ev.append({"tick": tk, "actor": alias, "op": "look", "target": cx.target(a.to),
                       "until": tick(until) if until is not None else None})
        elif a.do == "pose":
            p = lib.poses.get(a.name)
            if p is None:
                cx.warnings.append(f"no pose {a.name!r}")
                continue
            b.pose(p, t, a.ease)
        elif a.do == "gesture":
            g = lib.gestures.get(a.name)
            if g is None:
                cx.warnings.append(f"no gesture {a.name!r}")
                continue
            b.gestures.append(gesture_keys(g, t, until))
        elif a.do == "sit":
            seat = cx.points.get(a.on) if a.on else None
            if seat is not None:
                ev.append({"tick": tk, "actor": alias, "op": "navigate", "target": {"pos": list(seat.pos)},
                           "speed": WALK_SPEED, "then": {"op": "place", "pos": list(seat.pos), "yaw": seat.yaw}})
            p = lib.poses.get("sit")
            if p:
                b.pose(p, t, a.ease)
        elif a.do == "stand":
            b.pose(lib.poses.get("stand", Pose()), t, a.ease)
        elif a.do in ("hold", "wear"):
            prop = cx.reg.prop(a.item)
            slot = ("mainhand" if a.hand == "main" else "offhand") if a.do == "hold" else a.slot
            ev.append({"tick": tk, "actor": alias, "op": "equip", "slot": slot, "item": prop.item if prop else ""})
        elif a.do == "use":
            ev.append({"tick": tk, "actor": alias, "op": "use", "target": cx.target(a.on)})
        elif a.do == "appear":
            pos = list(cx.points[a.on].pos) if a.on in cx.points else None
            ev.append({"tick": tk, "actor": alias, "op": "appear", "pos": pos})
        elif a.do == "leave":
            if a.on:
                ev.append({"tick": tk, "actor": alias, "op": "navigate", "target": cx.target(a.on),
                           "speed": WALK_SPEED, "then": {"op": "vanish"}})
            else:
                ev.append({"tick": tk, "actor": alias, "op": "vanish"})
        elif a.do == "particles":
            ev.append({"tick": tk, "actor": alias, "op": "particles", "name": a.name,
                       "target": cx.target(a.on) if a.on else {"actor": alias},
                       "until": tick(until) if until is not None else tk + TPS})
    if a.do == "dialogue":
        ev += _dialogue(a, t, until, cx, bodies, lib, length, rng)
    return ev


def _dialogue(a: Action, t: float, until: float | None, cx: _Ctx, bodies, lib: Library,
              length: float, rng: random.Random) -> list[dict]:
    """Turns: the speaker talks, everybody else looks at the speaker, listens, and now
    and then nods."""
    from .direction import Turn

    ev: list[dict] = []
    people = a.actors()
    end_all = until if until is not None else length
    turns = a.turns or [Turn(speaker=people[0])]
    ends: list[float | None] = []
    for k, turn in enumerate(turns):
        try:
            ends.append(cx.t(turn.until) if turn.until is not None else None)
        except Exception as ex:
            cx.warnings.append(f"dialogue turn {k + 1}: {ex}")
            ends.append(None)
    if ends[-1] is None:
        ends[-1] = end_all
    # a turn with no end of its own shares what is left before the next known end
    starts = [t] + [0.0] * (len(turns) - 1)
    for k in range(len(turns)):
        if k:
            starts[k] = ends[k - 1]
        if ends[k] is None:
            later = next(ends[j] for j in range(k + 1, len(turns)) if ends[j] is not None)
            open_n = next(j for j in range(k + 1, len(turns)) if ends[j] is not None) - k + 1
            ends[k] = starts[k] + (later - starts[k]) / open_n
    talk, listen, nod = lib.gestures.get("talk"), lib.gestures.get("listen"), lib.gestures.get("nod")
    for turn, s, e in zip(turns, starts, ends):
        for p in people:
            b = bodies.setdefault(p, _Body())
            if p == turn.speaker:
                if talk:
                    b.gestures.append(gesture_keys(talk, s, e))
                other = next((q for q in people if q != p), None)
                if other:
                    ev.append({"tick": tick(s), "actor": p, "op": "look", "target": {"actor": other},
                               "until": tick(e)})
                continue
            ev.append({"tick": tick(s), "actor": p, "op": "look", "target": {"actor": turn.speaker},
                       "until": tick(e)})
            if listen:
                b.gestures.append(gesture_keys(listen, s, e))
            if nod:
                at = s + rng.uniform(1.0, 2.5)
                while at + nod.length < e:
                    b.gestures.append(gesture_keys(nod, at))
                    at += rng.uniform(2.5, 5.0)
    # at the start everybody turns to face the conversation: two people face each
    # other, a larger group faces whoever speaks first
    first = turns[0].speaker
    for p in people:
        other = (people[1] if p == people[0] else people[0]) if len(people) == 2 else first
        if other != p:
            ev.append({"tick": tick(t), "actor": p, "op": "turn", "target": {"actor": other}})
    return ev


def _crowd(k: int, cr, people: list, cells: list, world: World, cx: _Ctx, bodies, lib: Library,
           length: float, line: Line | None) -> tuple[list[dict], list[dict]]:
    """Extras as actors, and what each of them does over the take."""
    actors, ev = [], []
    rng = random.Random(f"crowd-behaviour|{k}|{cr.seed}")
    talk = lib.gestures.get("talk")
    look_around = lib.gestures.get("look_around")
    nod = lib.gestures.get("nod")
    sit = lib.poses.get("sit")
    ok_cells = [c for c in cells if world.standable(c)]
    groups: dict[int, list[str]] = {}
    for n, x in enumerate(people):
        found = cx.reg.variant(x.face)
        var = found[1] if found else None
        alias = f"x{k + 1}_{n + 1}"
        actors.append({"id": alias, "face": x.face, "preset": var.preset if var else "",
                       "body": var.body if var else "humanoid", "pos": list(x.pos), "yaw": x.yaw,
                       "scale": 1.0, "equip": {}, "hidden": False, "extra": True})
        b = bodies.setdefault(alias, _Body())
        b.pose(lib.poses.get("stand", Pose()), 0.0, "step")
        phase = rng.uniform(0, 3)
        if cr.do == "sit" and sit:
            b.pose(sit, 0.0, "step")
        if cr.do == "wander" and ok_cells:
            at = phase
            while at < length - 1:
                c = rng.choice(ok_cells)
                ev.append({"tick": tick(at), "actor": alias, "op": "navigate",
                           "target": {"pos": list(crowds.centre(c))}, "speed": WALK_SPEED * rng.uniform(0.8, 1.0)})
                at += rng.uniform(3.0, 7.0)
        elif cr.do == "flow" and line is not None and line.points:
            # each walker sets off from the start of the route at its own moment,
            # walks it to the end and vanishes; spacing them through the take is
            # what makes it a stream rather than a column
            start = length * n / max(1, len(people)) - PREROLL
            ev.append({"tick": tick(max(0.0, start)), "actor": alias, "op": "appear",
                       "pos": list(line.points[0])})
            ev.append({"tick": tick(max(0.0, start)) + 1, "actor": alias, "op": "navigate",
                       "target": {"pos": list(line.points[-1])}, "speed": WALK_SPEED * rng.uniform(0.85, 1.1),
                       "via": [list(p) for p in line.points[1:-1]], "then": {"op": "vanish"}})
            actors[-1]["hidden"] = True
        elif cr.do == "watch" and cr.toward:
            ev.append({"tick": 0, "actor": alias, "op": "look", "target": cx.target(cr.toward), "until": None})
        elif cr.do == "talk_groups":
            groups.setdefault(x.group, []).append(alias)
        if cr.do in ("stand", "queue", "watch", "sit") and look_around:
            at = phase + rng.uniform(2, 6)
            while at + look_around.length < length:
                b.gestures.append(gesture_keys(look_around, at))
                at += rng.uniform(6, 12)
    # chatting circles: the turn passes round the circle
    for g, members in groups.items():
        at = rng.uniform(0, 1.5)
        while at < length and talk:
            speaker = rng.choice(members)
            span = rng.uniform(2.0, 4.5)
            for m in members:
                if m == speaker:
                    bodies[m].gestures.append(gesture_keys(talk, at, min(at + span, length)))
                else:
                    ev.append({"tick": tick(at), "actor": m, "op": "look", "target": {"actor": speaker},
                               "until": tick(min(at + span, length))})
                    if nod and rng.random() < 0.4:
                        bodies[m].gestures.append(gesture_keys(nod, at + rng.uniform(0.5, span - 0.9)))
            at += span + rng.uniform(0.2, 0.8)
    return actors, ev
