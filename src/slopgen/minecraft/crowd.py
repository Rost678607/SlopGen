"""Extras: from "a crowd this dense, this mixed" to who stands where wearing which face.

The operator configures a crowd rather than listing it — a density over a zone and
the proportions of characters and of their faces, any of them excluded (see
`direction.Crowd`). Writing out thirty extras one by one is what neither a model nor
a person should do, and it is what makes crowds look cloned when they do.

Three things are decided here and only here:

* HOW MANY — density over the standable cells, capped;
* WHICH FACES — the proportions apportioned by largest remainder, so 60/30/10 of
  seven is 4/2/1 and not 4/2/0;
* WHO STANDS WHERE — positions spread out (or arranged, for a queue, seats or
  chatting groups), and faces dealt so that neighbours differ.

What they then DO over time is `compile`'s, from the behaviour named here.
Everything is seeded, so the same crowd comes out the same on every draft — a draft
that reshuffled the extras each time could not be compared with the last one.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .direction import Crowd
from .registry import Line, Point, Registry
from .voxels import Cell, World

# The densest a crowd gets: one person per two standable cells. Denser than that
# Easy NPC's bodies start pushing each other off their spots.
PACKING = 0.5
# The global ceiling on extras in a scene, whatever the density says: every one is a
# whole NPC, ticking, pathfinding and being recorded.
CAP = 30
SPACING = 1.4  # blocks between two extras placed freely
QUEUE_GAP = 0.9  # blocks between two people in a queue
GROUP_SIZES = (2, 3, 3, 4, 5)


@dataclass
class Extra:
    face: str  # "Character/face"
    pos: tuple[float, float, float]  # feet, block-centre
    yaw: float
    group: int = -1  # talk_groups: which circle; queue: place in line
    seat: str = ""  # sit: the seat point's id


def faces(cr: Crowd, reg: Registry) -> dict[str, float]:
    """The crowd's mix as weights per FACE, exclusions applied, summing to 1.

    A character named without a face spreads its share evenly over its faces: the
    operator said "Практиканты 60 %", not which practicants."""
    excluded: set[str] = set()
    for ref in cr.exclude:
        name, _, face = ref.partition("/")
        ch = reg.character(name)
        if ch is None:
            continue
        if face in ("", "*"):
            excluded |= {f"{ch.name}/{v.name}" for v in ch.variants}
        else:
            excluded.add(f"{ch.name}/{face}")
    w: dict[str, float] = {}
    for ref, share in cr.mix.items():
        if share <= 0:
            continue
        name, _, face = ref.partition("/")
        ch = reg.character(name)
        if ch is None:
            continue
        pool = ([f"{ch.name}/{v.name}" for v in ch.variants] if face in ("", "*")
                else [f"{ch.name}/{face}"])
        pool = [f for f in pool if f not in excluded]
        for f in pool:
            w[f] = w.get(f, 0.0) + share / len(pool)
    total = sum(w.values())
    return {f: v / total for f, v in w.items()} if total else {}


def apportion(weights: dict[str, float], n: int) -> dict[str, int]:
    """Largest-remainder rounding: counts that add up to `n` and stay as close to the
    proportions as whole people can."""
    if n <= 0 or not weights:
        return {}
    raw = {k: v * n for k, v in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    rest = n - sum(out.values())
    for k in sorted(raw, key=lambda k: (raw[k] - out[k], weights[k]), reverse=True)[:rest]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}


def headcount(cr: Crowd, cells: int, cap: int = CAP) -> int:
    n = round(cr.density * PACKING * cells)
    if cr.max:
        n = min(n, cr.max)
    return max(0, min(n, cap))


def yaw_to(a, b) -> float:
    """Minecraft yaw from a towards b: 0 faces +Z, 90 faces -X."""
    dx, dz = b[0] - a[0], b[2] - a[2]
    return math.degrees(math.atan2(-dx, dz))


def centre(c: Cell) -> tuple[float, float, float]:
    return (c[0] + 0.5, float(c[1]), c[2] + 0.5)


def spread(cells: list[Cell], n: int, rng: random.Random, spacing: float = SPACING) -> list[Cell]:
    """Up to `n` cells at least `spacing` apart, chosen at random — the cheap blue
    noise of trying each cell once in a shuffled order."""
    pool = list(cells)
    rng.shuffle(pool)
    out: list[Cell] = []
    for c in pool:
        if len(out) >= n:
            break
        if all(math.dist(centre(c), centre(o)) >= spacing for o in out):
            out.append(c)
    return out


def deal(positions: list[tuple[float, float, float]], counts: dict[str, int],
         rng: random.Random) -> list[str]:
    """Faces for positions so that the same face is as far from itself as the counts
    allow. Greedy: each position takes, of the faces still owed, the one whose nearest
    twin is furthest away.

    The positions are visited as a nearest-neighbour chain from a random start, not in
    a random order: the greedy choice only sees faces already dealt, so it has to be
    dealing NEXT to them for "furthest twin" to mean "not the neighbour's face"."""
    left = dict(counts)
    placed: dict[str, list[tuple[float, float, float]]] = {}
    out: list[str] = []
    order: list[int] = []
    if positions:
        rest = set(range(len(positions)))
        cur = rng.randrange(len(positions))
        while True:
            order.append(cur)
            rest.discard(cur)
            if not rest:
                break
            cur = min(sorted(rest), key=lambda j: math.dist(positions[cur], positions[j]))
    assigned: dict[int, str] = {}
    for idx in order:
        p = positions[idx]
        best, best_d = "", -1.0
        for face, k in sorted(left.items()):
            if k <= 0:
                continue
            d = min((math.dist(p, q) for q in placed.get(face, [])), default=math.inf)
            # prefer the face with more still owed when the distances tie, so the
            # last positions are not left with one face to fill a corner
            score = d + k * 1e-3 + rng.random() * 1e-6
            if score > best_d:
                best, best_d = face, score
        if not best:
            break
        assigned[idx] = best
        left[best] -= 1
        placed.setdefault(best, []).append(p)
    for i in range(len(positions)):
        out.append(assigned.get(i, ""))
    return out


def assign(cr: Crowd, reg: Registry, world: World, cells: list[Cell], *,
           seats: list[Point] = (), line: Line | None = None,
           toward: tuple[float, float, float] | None = None,
           taken: set[Cell] = frozenset(), cap: int = CAP) -> list[Extra]:
    """Place one crowd. `cells` are the zone's feet cells; the forbidden ones and the
    ones the cast already stands in (`taken`) must already be out of it, or be
    passed in `taken`."""
    rng = random.Random(f"crowd|{cr.area}|{cr.seed}")
    ok = [c for c in cells if c not in taken and world.standable(c)]
    weights = faces(cr, reg)
    if not weights:
        return []

    if cr.do == "sit":
        free = [s for s in seats]
        n = min(headcount(cr, len(ok), cap), len(free)) if ok else min(len(free), cap)
        rng.shuffle(free)
        chosen = free[:n]
        pos = [s.pos for s in chosen]
        names = deal(pos, apportion(weights, len(pos)), rng)
        return [Extra(f, s.pos, s.yaw, seat=s.id) for f, s in zip(names, chosen) if f]

    if cr.do == "queue" and line is not None and line.points:
        length = line.length()
        n = min(headcount(cr, len(ok), cap) or cap, int(length / QUEUE_GAP) + 1)
        pos, yaws = [], []
        for k in range(n):
            f = (k * QUEUE_GAP) / length if length else 0.0
            p = line.at(f)
            ahead = line.at(max(f - 0.05, 0.0)) if k else (toward or line.at(0.0))
            pos.append(p)
            yaws.append(yaw_to(p, ahead) if ahead != p else 0.0)
        names = deal(pos, apportion(weights, len(pos)), rng)
        return [Extra(f, p, y, group=k) for k, (f, p, y) in enumerate(zip(names, pos, yaws)) if f]

    n = headcount(cr, len(ok), cap)
    if cr.do == "talk_groups":
        return _groups(cr, ok, n, weights, world, rng)

    spots = spread(ok, n, rng)
    pos = [centre(c) for c in spots]
    names = deal(pos, apportion(weights, len(pos)), rng)
    out = []
    for f, p in zip(names, pos):
        if not f:
            continue
        if cr.do == "watch" and toward is not None:
            yaw = yaw_to(p, toward) + rng.uniform(-12, 12)
        else:
            yaw = rng.uniform(-180, 180)
        out.append(Extra(f, p, yaw))
    return out


def _groups(cr: Crowd, ok: list[Cell], n: int, weights, world: World,
            rng: random.Random) -> list[Extra]:
    """Circles of two to five, everyone facing the middle of their own circle."""
    okset = set(ok)
    centres = spread(ok, max(1, n // 2), rng, spacing=3.5)
    pos: list[tuple[float, float, float]] = []
    yaws: list[float] = []
    groups: list[int] = []
    used: set[Cell] = set()
    for gi, c in enumerate(centres):
        if len(pos) >= n:
            break
        size = min(rng.choice(GROUP_SIZES), n - len(pos))
        if size < 2:
            break
        mid = centre(c)
        phase = rng.uniform(0, 2 * math.pi)
        members = []
        for k in range(size):
            ang = phase + 2 * math.pi * k / size
            r = 1.0 if size <= 3 else 1.3
            cell = (math.floor(mid[0] + r * math.cos(ang)), c[1], math.floor(mid[2] + r * math.sin(ang)))
            if cell in okset and cell not in used:
                members.append(cell)
        if len(members) < 2:
            continue
        for cell in members:
            used.add(cell)
            p = centre(cell)
            pos.append(p)
            yaws.append(yaw_to(p, mid))
            groups.append(gi)
    names = deal(pos, apportion(weights, len(pos)), rng)
    return [Extra(f, p, y, group=g) for f, p, y, g in zip(names, pos, yaws, groups) if f]
