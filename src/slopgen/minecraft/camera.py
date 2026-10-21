"""Where the camera goes, so that what it films is never hidden.

A shot asks for a view — in a character's face at 2.5 blocks, over a shoulder,
orbiting — and the world answers with walls, pillars and other people. The operator's
rule is that an obstruction is never a reason to drop the shot: the camera flies in
closer, or takes a path nothing blocks. So this is a search, not a formula.

It runs AFTER the take is played, on where everybody actually was (the mod reports
positions per tick), because Easy NPC walks with its own pathfinding and nobody knows
in advance which side of the pillar it will pass.

The search, per shot:

1. at each step (`STEP` seconds) candidate camera positions are laid around the
   target — angles × distances × heights around the ordered view, the ordered one
   first, nearer ones included;
2. a candidate is dropped if it sits in a block or anything opaque — or another
   body — stands between it and the target's eyes;
3. a move from one step to the next is allowed only along a clear segment and below a
   speed limit, so the camera never visibly passes through a wall;
4. dynamic programming picks the path with the least "distance from what was ordered
   + jolts", and the path is smoothed where smoothing stays clear.

A target nobody can see even from up close is walled in, which is a mistake of
staging rather than of the camera; that, and a fixed or rail camera whose target goes
behind something (those cameras do not move off their mark), are REPORTED with the
time span, for the director's repair round and the operator's panel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .easing import ease, lerp
from .voxels import Vec, World

STEP = 0.1  # seconds between path samples
MIN_DISTANCE = 0.7  # blocks: nearer than this the near plane is in the actor's face
MAX_SPEED = 12.0  # blocks per second the camera may travel while searching
KEEP = 24  # feasible candidates kept per step
ANGLES = (0, 15, -15, 30, -30, 45, -45, 60, -60, 90, -90, 135, -135, 180)
SCALES = (1.0, 0.8, 0.65, 0.5, 0.38, 0.28, 0.2)
LIFTS = (0.0, 12.0, 25.0, -8.0)  # degrees of elevation added to the ordered one
EYE = 1.62  # eye height of a standing humanoid; the mod reports the real one per actor


@dataclass
class Body:
    """Where an actor was at one moment: feet, facing, eye height."""

    pos: Vec
    yaw: float
    eye: float = EYE

    @property
    def eyes(self) -> Vec:
        return (self.pos[0], self.pos[1] + self.eye, self.pos[2])


@dataclass
class Track:
    """An actor's recorded positions, one per tick (1/20 s), from the take."""

    bodies: list[Body]
    rate: float = 20.0

    def at(self, t: float) -> Body:
        if not self.bodies:
            raise ValueError("empty track")
        k = max(0.0, min(t * self.rate, len(self.bodies) - 1))
        i = int(k)
        j = min(i + 1, len(self.bodies) - 1)
        f = k - i
        a, b = self.bodies[i], self.bodies[j]
        pos = tuple(lerp(a.pos[n], b.pos[n], f) for n in range(3))
        return Body(pos, _lerp_yaw(a.yaw, b.yaw, f), lerp(a.eye, b.eye, f))

    def smooth_yaw(self, t: float, window: float = 0.6) -> float:
        """Facing averaged over a moment, so a camera that keeps to somebody's face
        does not shiver with every twitch of their head-tracking."""
        xs = ys = 0.0
        n = 7
        for k in range(n):
            y = math.radians(self.at(t + (k / (n - 1) - 0.5) * window).yaw)
            xs += math.cos(y)
            ys += math.sin(y)
        return math.degrees(math.atan2(ys, xs))


def _lerp_yaw(a: float, b: float, f: float) -> float:
    d = (b - a + 180.0) % 360.0 - 180.0
    return a + d * f


@dataclass
class Sample:
    t: float
    pos: Vec
    yaw: float
    pitch: float
    fov: float


@dataclass
class Issue:
    start: float
    end: float
    what: str

    def __str__(self) -> str:
        return f"{self.what} at {self.start:.1f}–{self.end:.1f}s"


@dataclass
class Path:
    samples: list[Sample] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------


def facing(yaw: float) -> Vec:
    """Minecraft yaw → unit vector on the ground: 0 faces +Z, 90 faces -X."""
    r = math.radians(yaw)
    return (-math.sin(r), 0.0, math.cos(r))


def look(cam: Vec, at: Vec) -> tuple[float, float]:
    """Yaw and pitch for a camera at `cam` looking at `at` (pitch positive = down)."""
    dx, dy, dz = at[0] - cam[0], at[1] - cam[1], at[2] - cam[2]
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = -math.degrees(math.atan2(dy, math.hypot(dx, dz)))
    return yaw, pitch


def orbit_point(centre: Vec, azimuth: float, elevation: float, dist: float) -> Vec:
    """A point `dist` away from `centre`, in the direction a body facing `azimuth`
    (Minecraft yaw) would be looking, raised by `elevation` degrees."""
    f = facing(azimuth)
    e = math.radians(elevation)
    return (centre[0] + f[0] * dist * math.cos(e),
            centre[1] + dist * math.sin(e),
            centre[2] + f[2] * dist * math.cos(e))


def _ray_hits_box(a: Vec, b: Vec, lo: Vec, hi: Vec) -> bool:
    t0, t1 = 0.0, 1.0
    for i in range(3):
        d = b[i] - a[i]
        if abs(d) < 1e-9:
            if a[i] < lo[i] or a[i] > hi[i]:
                return False
            continue
        u0, u1 = (lo[i] - a[i]) / d, (hi[i] - a[i]) / d
        if u0 > u1:
            u0, u1 = u1, u0
        t0, t1 = max(t0, u0), min(t1, u1)
        if t0 > t1:
            return False
    return True


def body_box(b: Body, half: float = 0.3) -> tuple[Vec, Vec]:
    return ((b.pos[0] - half, b.pos[1], b.pos[2] - half),
            (b.pos[0] + half, b.pos[1] + b.eye + 0.2, b.pos[2] + half))


@dataclass
class Scene3D:
    """What a camera has to see past: the blocks, and every OTHER body at a moment."""

    world: World
    others: dict[str, Track] = field(default_factory=dict)
    through_glass: bool = True

    def clear(self, cam: Vec, eyes: Vec, t: float, exclude: tuple[str, ...] = ()) -> bool:
        if not self.world.sees(cam, eyes, self.through_glass):
            return False
        for name, tr in self.others.items():
            if name in exclude:
                continue
            lo, hi = body_box(tr.at(t))
            if _ray_hits_box(cam, eyes, lo, hi):
                return False
        return True


# --------------------------------------------------------------------------
# a camera riding a character
# --------------------------------------------------------------------------

SIDE_ANGLE = {"front": 0.0, "back": 180.0, "left": 90.0, "right": -90.0,
              "shoulder_left": 160.0, "shoulder_right": -160.0, "orbit": 0.0}


def ride(target: Track, name: str, scene: Scene3D, *, duration: float, side: str = "front",
         distance: tuple[float, float] = (3.0, 3.0), height: float = 0.0, orbit: float = 90.0,
         fov: tuple[float, float] = (70.0, 70.0), easing: str = "smooth",
         aim: Track | None = None, aim_name: str = "", t0: float = 0.0) -> Path:
    """A camera on a character for `duration` seconds starting at take time `t0`.

    `aim` is who the camera LOOKS at when that is not the rider — an over-the-shoulder
    shot rides the listener and looks at the speaker."""
    steps = max(1, int(round(duration / STEP)))
    times = [t0 + min(k * STEP, duration) for k in range(steps + 1)]
    layers: list[list[tuple[Vec, float]]] = []  # per step: (position, static cost)
    issues: list[Issue] = []
    exclude = (name,) + ((aim_name,) if aim_name else ())

    for t in times:
        f = (t - t0) / duration if duration else 1.0
        e = ease(easing, f)
        body = target.at(t)
        look_at = (aim.at(t).eyes if aim else body.eyes)
        d0 = lerp(distance[0], distance[1], e)
        az0 = target.smooth_yaw(t) + SIDE_ANGLE.get(side, 0.0)
        if side == "orbit":
            az0 += orbit * e
        lift0 = math.degrees(math.atan2(height, d0)) if d0 else 0.0
        centre = body.eyes
        if side.startswith("shoulder"):
            # behind one shoulder, a little above it: the rider's own head and back in
            # the near corner of the frame
            sidev = facing(target.smooth_yaw(t) + (90.0 if side == "shoulder_left" else -90.0))
            centre = (centre[0] + sidev[0] * 0.45, centre[1] + 0.15, centre[2] + sidev[2] * 0.45)
        cands = _candidates(centre, az0, lift0, d0)
        kept = []
        for cost, p in cands:
            if not scene.world.free_at(p):
                continue
            if not scene.clear(p, look_at, t, exclude):
                continue
            kept.append((p, cost))
            if len(kept) >= KEEP:
                break
        layers.append(kept)

    path_pos = _viterbi(layers, scene.world)
    # steps nobody could see the target from are filled by holding the nearest seen
    # position, and reported
    blind = [i for i, layer in enumerate(layers) if not layer]
    for a, b in _runs(blind):
        issues.append(Issue(times[a] - t0, times[b] - t0, f"{aim_name or name} cannot be seen from anywhere near (walled in?)"))
    path_pos = _fill(path_pos)
    if path_pos is None:
        return Path([], issues or [Issue(0, duration, f"{name} cannot be seen at all")])
    smooth = _smooth(path_pos, layers, times, scene, target, aim, exclude)
    out = []
    for t, p in zip(times, smooth):
        f = (t - t0) / duration if duration else 1.0
        look_at = (aim.at(t).eyes if aim else target.at(t).eyes)
        yaw, pitch = look(p, look_at)
        out.append(Sample(t - t0, p, yaw, pitch, lerp(fov[0], fov[1], ease(easing, f))))
    return Path(out, issues)


def _candidates(centre: Vec, az0: float, lift0: float, d0: float) -> list[tuple[float, Vec]]:
    """Every candidate around the ordered view, cheapest (= closest to what was
    ordered) first."""
    out = []
    for da in ANGLES:
        for s in SCALES:
            d = max(MIN_DISTANCE, d0 * s)
            for dl in LIFTS:
                cost = (da / 45.0) ** 2 + (math.log(d0 / d) if d0 > d else 0.0) * 1.5 + (dl / 25.0) ** 2 * 0.6
                out.append((cost, orbit_point(centre, az0 + da, lift0 + dl, d)))
    out.sort(key=lambda c: c[0])
    return out


def _viterbi(layers: list[list[tuple[Vec, float]]], world: World) -> list[Vec | None]:
    """The cheapest chain through the candidate layers; a layer with no candidate is
    skipped over (its position is None, filled in later)."""
    n = len(layers)
    best: list[list[float]] = []
    back: list[list[int]] = []
    prev_layer: list[tuple[Vec, float]] = []
    prev_cost: list[float] = []
    prev_idx = -1
    vmax = MAX_SPEED * STEP
    for i, layer in enumerate(layers):
        costs, links = [], []
        for p, c in layer:
            if not prev_layer:
                costs.append(c)
                links.append(-1)
                continue
            gap = i - prev_idx
            best_c, best_j = math.inf, -1
            for j, (q, _) in enumerate(prev_layer):
                d = math.dist(p, q)
                if d > vmax * gap:
                    continue
                mid = tuple((p[k] + q[k]) / 2 for k in range(3))
                if d > 0.3 and not world.free_at(mid, 0.1):
                    continue
                total = prev_cost[j] + c + (d / (vmax * gap)) ** 2 * 0.8
                if total < best_c:
                    best_c, best_j = total, j
            if best_j < 0:
                # nothing reachable: allow a cut-free jump at a high price rather than
                # no path — still never through a wall (`free_at` on both ends holds)
                j = min(range(len(prev_layer)), key=lambda j: math.dist(p, prev_layer[j][0]))
                best_c, best_j = prev_cost[j] + c + 50.0, j
            costs.append(best_c)
            links.append(best_j)
        best.append(costs)
        back.append(links)
        if layer:
            prev_layer, prev_cost, prev_idx = layer, costs, i
    # walk back from the cheapest end
    out: list[Vec | None] = [None] * n
    last = max((i for i in range(n) if layers[i]), default=-1)
    if last < 0:
        return out
    j = min(range(len(best[last])), key=lambda j: best[last][j])
    i = last
    while i >= 0 and j >= 0:
        out[i] = layers[i][j][0]
        j = back[i][j]
        i -= 1
        while i >= 0 and not layers[i]:
            i -= 1
    return out


def _fill(path: list[Vec | None]) -> list[Vec] | None:
    if all(p is None for p in path):
        return None
    out = list(path)
    first = next(p for p in out if p is not None)
    last = first
    for i, p in enumerate(out):
        if p is None:
            out[i] = last
        else:
            last = p
    # leading gap took `first` already via `last` initialised to it
    return out  # type: ignore[return-value]


def _runs(idx: list[int]) -> list[tuple[int, int]]:
    out = []
    for i in idx:
        if out and out[-1][1] == i - 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _smooth(path: list[Vec], layers, times, scene: Scene3D, target: Track,
            aim: Track | None, exclude) -> list[Vec]:
    """A centred moving average, kept only where the averaged point is still free and
    still sees the target — smoothing must never undo what the search was for."""
    k = 3
    out = []
    for i, p in enumerate(path):
        lo, hi = max(0, i - k), min(len(path), i + k + 1)
        avg = tuple(sum(q[n] for q in path[lo:hi]) / (hi - lo) for n in range(3))
        t = times[i]
        look_at = aim.at(t).eyes if aim else target.at(t).eyes
        if scene.world.free_at(avg) and scene.clear(avg, look_at, t, exclude):
            out.append(avg)
        else:
            out.append(p)
    return out


# --------------------------------------------------------------------------
# cameras bolted to the world
# --------------------------------------------------------------------------


def fixed(pos: Vec, scene: Scene3D, *, duration: float, aim: Track | None = None,
          aim_name: str = "", aim_point: Vec | None = None, yaw: float = 0.0, pitch: float = 0.0,
          fov: tuple[float, float] = (70.0, 70.0), easing: str = "smooth", t0: float = 0.0) -> Path:
    """A camera that stays on its mark, turning to its target. It does not move off
    the mark to see past anything — that is what a fixed camera IS — so an occlusion
    is reported, not solved."""
    return _bolted(lambda f: pos, scene, duration=duration, aim=aim, aim_name=aim_name,
                   aim_point=aim_point, yaw=yaw, pitch=pitch, fov=fov, easing=easing, t0=t0)


def rail(points_at, scene: Scene3D, *, duration: float, travel: tuple[float, float] = (0.0, 1.0),
         aim: Track | None = None, aim_name: str = "", aim_point: Vec | None = None,
         fov: tuple[float, float] = (70.0, 70.0), easing: str = "smooth", t0: float = 0.0) -> Path:
    """A camera travelling a marked line (`points_at(f)` → position) from `travel[0]` to
    `travel[1]` of its length."""
    return _bolted(lambda f: points_at(lerp(travel[0], travel[1], f)), scene, duration=duration,
                   aim=aim, aim_name=aim_name, aim_point=aim_point, fov=fov, easing=easing, t0=t0)


def _bolted(where, scene: Scene3D, *, duration, aim, aim_name, aim_point, fov, easing, t0,
            yaw: float = 0.0, pitch: float = 0.0) -> Path:
    steps = max(1, int(round(duration / STEP)))
    out, hidden = [], []
    for k in range(steps + 1):
        rel = min(k * STEP, duration)
        f = ease(easing, rel / duration if duration else 1.0)
        p = where(f)
        t = t0 + rel
        if aim is not None:
            target = aim.at(t).eyes
        elif aim_point is not None:
            target = aim_point
        else:
            target = None
        if target is not None:
            y, pi = look(p, target)
            if not scene.clear(p, target, t, (aim_name,) if aim_name else ()):
                hidden.append(k)
        else:
            y, pi = yaw, pitch
        out.append(Sample(rel, p, y, pi, lerp(fov[0], fov[1], f)))
    issues = [Issue(a * STEP, b * STEP, f"{aim_name or 'the target'} is hidden from this camera")
              for a, b in _runs(hidden)]
    return Path(out, issues)
