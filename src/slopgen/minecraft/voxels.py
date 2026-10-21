"""The world around a place, as the geometry here needs it: what is solid, and what
can be seen through.

Two questions and they are different. SOLID is about bodies — can somebody stand
here, can the camera be here without being inside a wall. OPAQUE is about sight —
does this block hide what is behind it. Glass is the case that tells them apart: you
cannot walk through it and you can film through it, and the operator wanted it to
stay that way by default.

The mod sends both as bit grids over a box around the place (see `from_payload`),
because a place is a few thousand blocks and nobody wants to ship a list of tuples
for it.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass, field

Vec = tuple[float, float, float]
Cell = tuple[int, int, int]


@dataclass
class World:
    origin: Cell
    size: Cell
    solid: bytearray
    opaque: bytearray
    # Outside the grid nothing is known. It is treated as empty: a ray leaving the
    # box is a camera looking at open sky, and refusing it would refuse every
    # exterior shot.
    _n: int = field(init=False)

    def __post_init__(self):
        sx, sy, sz = self.size
        self._n = sx * sy * sz

    @classmethod
    def empty(cls, origin: Cell, size: Cell) -> "World":
        n = (size[0] * size[1] * size[2] + 7) // 8
        return cls(origin, size, bytearray(n), bytearray(n))

    @classmethod
    def from_payload(cls, data: dict) -> "World":
        """`{"origin": [x,y,z], "size": [sx,sy,sz], "solid": b64, "opaque": b64}`,
        bits little-endian within a byte, index `(y*sz + z)*sx + x` — the layout the
        mod writes (see `slopcam` `WorldScan`)."""
        return cls(tuple(data["origin"]), tuple(data["size"]),
                   bytearray(base64.b64decode(data["solid"])),
                   bytearray(base64.b64decode(data["opaque"])))

    def to_payload(self) -> dict:
        return {"origin": list(self.origin), "size": list(self.size),
                "solid": base64.b64encode(bytes(self.solid)).decode(),
                "opaque": base64.b64encode(bytes(self.opaque)).decode()}

    def _index(self, c: Cell) -> int:
        x, y, z = (c[i] - self.origin[i] for i in range(3))
        sx, sy, sz = self.size
        if not (0 <= x < sx and 0 <= y < sy and 0 <= z < sz):
            return -1
        return (y * sz + z) * sx + x

    @staticmethod
    def _get(bits: bytearray, i: int) -> bool:
        return i >= 0 and bool(bits[i >> 3] >> (i & 7) & 1)

    @staticmethod
    def _set(bits: bytearray, i: int, v: bool) -> None:
        if i < 0:
            return
        if v:
            bits[i >> 3] |= 1 << (i & 7)
        else:
            bits[i >> 3] &= ~(1 << (i & 7))

    def set(self, c: Cell, solid: bool = True, opaque: bool | None = None) -> None:
        i = self._index(c)
        self._set(self.solid, i, solid)
        self._set(self.opaque, i, solid if opaque is None else opaque)

    def is_solid(self, c: Cell) -> bool:
        return self._get(self.solid, self._index(c))

    def is_opaque(self, c: Cell) -> bool:
        return self._get(self.opaque, self._index(c))

    def standable(self, c: Cell) -> bool:
        """Feet and head free, floor under the feet."""
        x, y, z = c
        return (not self.is_solid(c) and not self.is_solid((x, y + 1, z))
                and self.is_solid((x, y - 1, z)))

    def free_at(self, p: Vec, margin: float = 0.2) -> bool:
        """A point (the camera) is clear of solid blocks by at least `margin`, so the
        near plane never clips into a wall it is pressed against."""
        for dx in (-margin, margin):
            for dy in (-margin, margin):
                for dz in (-margin, margin):
                    if self.is_solid(cell_of((p[0] + dx, p[1] + dy, p[2] + dz))):
                        return False
        return True

    def sees(self, a: Vec, b: Vec, through_glass: bool = True) -> bool:
        """Nothing opaque (or nothing solid, with `through_glass` off) between two
        points. The cells the points themselves sit in are not counted: the target's
        own eye cell is not a wall."""
        grid = self.opaque if through_glass else self.solid
        start, end = cell_of(a), cell_of(b)
        for c in traverse(a, b):
            if c == start or c == end:
                continue
            if self._get(grid, self._index(c)):
                return False
        return True


def cell_of(p: Vec) -> Cell:
    return (math.floor(p[0]), math.floor(p[1]), math.floor(p[2]))


def traverse(a: Vec, b: Vec):
    """Every cell a segment passes through, in order (Amanatides–Woo)."""
    x, y, z = cell_of(a)
    end = cell_of(b)
    d = [b[i] - a[i] for i in range(3)]
    step = [1 if v > 0 else -1 if v < 0 else 0 for v in d]
    t_max, t_delta = [], []
    for i, c in enumerate((x, y, z)):
        if d[i] == 0:
            t_max.append(math.inf)
            t_delta.append(math.inf)
        else:
            edge = c + (1 if step[i] > 0 else 0)
            t_max.append((edge - a[i]) / d[i])
            t_delta.append(abs(1 / d[i]))
    yield (x, y, z)
    cur = [x, y, z]
    for _ in range(10_000):
        if tuple(cur) == end:
            return
        i = t_max.index(min(t_max))
        if t_max[i] > 1.0:
            return
        cur[i] += step[i]
        t_max[i] += t_delta[i]
        yield tuple(cur)
