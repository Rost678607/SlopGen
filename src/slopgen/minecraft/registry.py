"""The world's markup: what there is to stage a scene in, as the operator marked it.

The model cannot see the world and the mod cannot read a story, so this is the
contract between them. It is written in game by the `slopcam` markup mode and in the
browser by the "Minecraft" tab, and it lives IN THE SAVE (`<save>/slopgen/
registry.json`) rather than beside the fandom: it is a list of coordinates, and
coordinates belong to the world they were taken in. The fandom only says which world
that is (`FandomConfig.minecraft`).

Two decisions shape it, both the operator's:

* A PLACE is a group, not a shape. It is how a model finds "the canteen" by what the
  narration says and how two doors in two rooms stop being the same door; nothing in
  it needs a boundary. There is no hierarchy of places either.
* Geometry is separate from meaning. Areas, points, lines and cameras are drawn
  first, and what they ARE — a zone people stand in, a seat, an entrance, a queue, a
  rail — is a list of roles set afterwards, more than one if it is more than one
  thing: a door is both a landmark and an entrance.

Names are what a direction refers to, so they are unique within a place; ids are
what the mod and the browser use, so a rename does not orphan anything that pointed
at the old name by id.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

REGISTRY_DIR = "slopgen"
REGISTRY_FILE = "registry.json"
VERSION = 1

AreaRole = Literal["zone", "forbidden"]
PointRole = Literal["landmark", "seat", "entry"]
LineRole = Literal["route", "queue", "rail"]
AREA_ROLES: tuple[str, ...] = ("zone", "forbidden")
POINT_ROLES: tuple[str, ...] = ("landmark", "seat", "entry")
LINE_ROLES: tuple[str, ...] = ("route", "queue", "rail")

Cell = tuple[int, int, int]


def new_id() -> str:
    return uuid.uuid4().hex[:10]


class Piece(BaseModel):
    """One piece of an area, added to it or cut out of it.

    Box, polygon and brush all add to the SAME area and each has its reverse, which
    is what lets an L-shaped hall be a box plus a box, or a room be a box minus the
    counter. Pieces apply in order, so a later piece wins.

    Cells are FEET cells — the block a creature stands in, one above the floor block
    the brush was pointed at — because every question asked of an area ("can somebody
    stand here?", "is this inside the zone?") is a question about where feet go."""

    op: Literal["add", "cut"] = "add"
    kind: Literal["box", "polygon", "cells"]
    # box: two opposite corners, inclusive block coordinates
    a: Cell | None = None
    b: Cell | None = None
    # polygon: a footprint on the XZ plane (block-centre coordinates) between two heights
    points: list[tuple[float, float]] = []
    y0: int = 0
    y1: int = 0
    # cells: painted feet cells
    cells: list[Cell] = []
    _cellset_cache: set[Cell] | None = PrivateAttr(default=None)

    def contains(self, c: Cell) -> bool:
        x, y, z = c
        if self.kind == "box" and self.a and self.b:
            lo = [min(p, q) for p, q in zip(self.a, self.b)]
            hi = [max(p, q) for p, q in zip(self.a, self.b)]
            return all(lo[i] <= c[i] <= hi[i] for i in range(3))
        if self.kind == "polygon":
            lo, hi = min(self.y0, self.y1), max(self.y0, self.y1)
            return lo <= y <= hi and _in_polygon(x + 0.5, z + 0.5, self.points)
        if self.kind == "cells":
            return c in self._cellset()
        return False

    def _cellset(self) -> set[Cell]:
        if self._cellset_cache is None or len(self._cellset_cache) != len(self.cells):
            self._cellset_cache = {tuple(c) for c in self.cells}
        return self._cellset_cache

    def bounds(self) -> tuple[Cell, Cell] | None:
        if self.kind == "box" and self.a and self.b:
            return (tuple(min(p, q) for p, q in zip(self.a, self.b)),
                    tuple(max(p, q) for p, q in zip(self.a, self.b)))
        if self.kind == "polygon" and self.points:
            xs = [p[0] for p in self.points]
            zs = [p[1] for p in self.points]
            return ((int(min(xs)) - 1, min(self.y0, self.y1), int(min(zs)) - 1),
                    (int(max(xs)) + 1, max(self.y0, self.y1), int(max(zs)) + 1))
        if self.kind == "cells" and self.cells:
            return (tuple(min(c[i] for c in self.cells) for i in range(3)),
                    tuple(max(c[i] for c in self.cells) for i in range(3)))
        return None


def _in_polygon(x: float, z: float, poly: list[tuple[float, float]]) -> bool:
    """Even-odd ray cast. A point exactly on an edge may go either way, which is fine
    for blocks: the brush is there for the cell somebody cares about."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, z1 = poly[i]
        x2, z2 = poly[(i + 1) % n]
        if (z1 > z) != (z2 > z):
            cross = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
            if x < cross:
                inside = not inside
    return inside


class Element(BaseModel):
    id: str = Field(default_factory=new_id)
    place: str = ""  # Place.id
    name: str
    description: str = ""


class Area(Element):
    roles: list[AreaRole] = ["zone"]
    pieces: list[Piece] = []

    def contains(self, c: Cell) -> bool:
        inside = False
        for p in self.pieces:
            if p.contains(c):
                inside = p.op == "add"
        return inside

    def bounds(self) -> tuple[Cell, Cell] | None:
        boxes = [b for p in self.pieces if p.op == "add" and (b := p.bounds())]
        if not boxes:
            return None
        return (tuple(min(b[0][i] for b in boxes) for i in range(3)),
                tuple(max(b[1][i] for b in boxes) for i in range(3)))

    def cells(self) -> list[Cell]:
        """Every feet cell inside the area, in a fixed order. Whether anybody can
        actually stand in one is the world's question, not the markup's — see
        `voxels.World.standable`."""
        bb = self.bounds()
        if bb is None:
            return []
        (x0, y0, z0), (x1, y1, z1) = bb
        return [(x, y, z) for y in range(y0, y1 + 1) for z in range(z0, z1 + 1)
                for x in range(x0, x1 + 1) if self.contains((x, y, z))]


class Point(Element):
    roles: list[PointRole] = ["landmark"]
    pos: tuple[float, float, float]
    yaw: float = 0.0  # Minecraft yaw, degrees: 0 = +Z (south), 90 = -X (west)


class Line(Element):
    roles: list[LineRole] = ["route"]
    points: list[tuple[float, float, float]] = []

    def length(self) -> float:
        return sum(_dist(a, b) for a, b in zip(self.points, self.points[1:]))

    def at(self, f: float) -> tuple[float, float, float]:
        """The point a fraction `f` of the way along the line, by distance."""
        if not self.points:
            raise ValueError(f"line {self.name!r} has no points")
        if len(self.points) == 1:
            return self.points[0]
        goal = max(0.0, min(1.0, f)) * self.length()
        for a, b in zip(self.points, self.points[1:]):
            d = _dist(a, b)
            if goal <= d and d > 0:
                k = goal / d
                return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))  # type: ignore[return-value]
            goal -= d
        return self.points[-1]


class Camera(Element):
    """A camera bolted to the world, as opposed to one riding a character (which is
    part of a shot, not of the markup). A fixed camera stands where it was put and may
    turn to its target and zoom; a rail camera travels a marked line."""

    kind: Literal["fixed", "rail"] = "fixed"
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    yaw: float = 0.0
    pitch: float = 0.0
    fov: float = 70.0
    rail: str = ""  # Line.id, for a rail camera
    screenshot: str = ""  # file under <save>/slopgen/shots/, "how it looks from here"


def _dist(a, b) -> float:
    return sum((a[i] - b[i]) ** 2 for i in range(3)) ** 0.5


class Place(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    description: str = ""
    screenshot: str = ""


class Variant(BaseModel):
    """One face of a character: an Easy NPC preset taken off an NPC standing in the
    world. `body` is the preset's model type (humanoid, spider, quadruped…) — the
    director is told it, because what a spider can do with a ladle is not what a
    humanoid can."""

    name: str
    preset: str = ""  # file under <save>/slopgen/actors/, written by the mod
    body: str = "humanoid"
    description: str = ""


class Character(BaseModel):
    name: str  # the fandom character (or estate) this is a face of
    variants: list[Variant] = []


class Prop(BaseModel):
    name: str
    item: str  # the item stack as SNBT, components and all
    description: str = ""


class Registry(BaseModel):
    version: int = VERSION
    places: list[Place] = []
    areas: list[Area] = []
    points: list[Point] = []
    lines: list[Line] = []
    cameras: list[Camera] = []
    characters: list[Character] = []
    props: list[Prop] = []

    # -- lookups -------------------------------------------------------------

    def place(self, name: str) -> Place | None:
        return next((p for p in self.places if p.name == name), None)

    def elements(self, place: Place) -> list[Element]:
        return [e for group in (self.areas, self.points, self.lines, self.cameras)
                for e in group if e.place == place.id]

    def find(self, place: Place, name: str, kinds: tuple[type, ...] = (Element,)) -> Element | None:
        return next((e for e in self.elements(place) if e.name == name and isinstance(e, kinds)), None)

    def by_id(self, eid: str) -> Element | None:
        return next((e for group in (self.areas, self.points, self.lines, self.cameras)
                     for e in group if e.id == eid), None)

    def character(self, name: str) -> Character | None:
        return next((c for c in self.characters if c.name == name), None)

    def variant(self, ref: str) -> tuple[Character, Variant] | None:
        """`"Character/variant"`. A bare character name is accepted only when it has a
        single face — a main actor names the face they wear, so an ambiguous one is a
        mistake to report, not a coin to toss."""
        name, _, var = ref.partition("/")
        c = self.character(name.strip())
        if c is None:
            return None
        if not var:
            return (c, c.variants[0]) if len(c.variants) == 1 else None
        v = next((v for v in c.variants if v.name == var.strip()), None)
        return (c, v) if v else None

    def prop(self, name: str) -> Prop | None:
        return next((p for p in self.props if p.name == name), None)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, save_dir: Path) -> "Registry":
        path = registry_path(save_dir)
        if not path.is_file():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, save_dir: Path) -> Path:
        """Atomically: the mod watches this file and must never read half of it."""
        path = registry_path(save_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".registry.", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return path


def registry_path(save_dir: Path) -> Path:
    return Path(save_dir) / REGISTRY_DIR / REGISTRY_FILE
