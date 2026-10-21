"""Poses and gestures: what an actor's body can be told to do, part by part.

Easy NPC plays keyframe animations only for the models of a separate mod, so for
ordinary NPCs every gesture is OURS: keys of rotation per body part, played by the
`slopcam` mod through Easy NPC's custom pose (`setModelPartRotation`) a tick at a
time. That is also what lets every transition carry its own interpolation, which the
operator asked for — on, off, and several curves (see `easing`).

A POSE is held: it is where the body goes and stays until the next pose. A GESTURE
is played: keys over a short time, once or looped, on top of whatever pose holds.

Angles are DEGREES in model space — x tips a part forward (negative raises an arm in
front), y turns it, z swings it out to the side — and the mod converts them for Easy
NPC. The built-in library below is the base set; the operator's own, made in the
editor of the markup mode, lives in the save (`<save>/slopgen/library.json`) and
wins on a name clash.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from .easing import Ease
from .registry import REGISTRY_DIR

LIBRARY_FILE = "library.json"

# The humanoid parts a key may move (Easy NPC `ModelPartType`). Other bodies have
# others — wings, a tail, four legs — and a key naming a part the body lacks is
# simply not played on that body.
PARTS: tuple[str, ...] = ("HEAD", "BODY", "RIGHT_ARM", "LEFT_ARM", "RIGHT_LEG", "LEFT_LEG",
                          "RIGHT_FRONT_LEG", "LEFT_FRONT_LEG", "RIGHT_HIND_LEG", "LEFT_HIND_LEG",
                          "RIGHT_WING", "LEFT_WING", "TAIL", "ROOT")

Rot = tuple[float, float, float]


class Key(BaseModel):
    t: float  # seconds from the start of the gesture
    rot: Rot
    ease: Ease = "smooth"  # how the part ARRIVES at this key


class Pose(BaseModel):
    description: str = ""
    parts: dict[str, Rot] = {}
    # a ROOT offset in blocks — sitting lowers the whole body onto the seat
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    vanilla: str = ""  # an entity pose Easy NPC has natively: CROUCHING, SLEEPING, SWIMMING
    settle: float = 0.35  # seconds to move into it


class Gesture(BaseModel):
    description: str = ""
    length: float
    loop: bool = False
    keys: dict[str, list[Key]] = {}


class Library(BaseModel):
    poses: dict[str, Pose] = {}
    gestures: dict[str, Gesture] = {}

    def merged(self, other: "Library") -> "Library":
        return Library(poses={**self.poses, **other.poses},
                       gestures={**self.gestures, **other.gestures})


def _k(t, x=0.0, y=0.0, z=0.0, ease="smooth") -> Key:
    return Key(t=t, rot=(x, y, z), ease=ease)


BUILTIN = Library(
    poses={
        "stand": Pose(description="standing, arms down — the neutral pose every other one returns to"),
        "sit": Pose(description="sitting on a seat, thighs forward",
                    parts={"RIGHT_LEG": (-90, 8, 0), "LEFT_LEG": (-90, -8, 0)},
                    offset=(0.0, -0.55, 0.0)),
        "crossed_arms": Pose(description="arms folded across the chest",
                             parts={"RIGHT_ARM": (-55, -35, 0), "LEFT_ARM": (-55, 35, 0)}),
        "hands_behind": Pose(description="hands clasped behind the back",
                             parts={"RIGHT_ARM": (25, 0, 10), "LEFT_ARM": (25, 0, -10)}),
        "hands_on_hips": Pose(description="hands on the hips, elbows out",
                              parts={"RIGHT_ARM": (-10, 0, 35), "LEFT_ARM": (-10, 0, -35)}),
        "head_down": Pose(description="head bowed — tired, ashamed, reading",
                          parts={"HEAD": (35, 0, 0)}),
        "lean_on": Pose(description="leaning forward on a counter or a desk with both hands",
                        parts={"BODY": (20, 0, 0), "RIGHT_ARM": (-60, 0, 0), "LEFT_ARM": (-60, 0, 0),
                               "HEAD": (-15, 0, 0)}),
        "crouch": Pose(description="crouching", vanilla="CROUCHING"),
        "lie": Pose(description="lying down", vanilla="SLEEPING"),
    },
    gestures={
        "talk": Gesture(description="talking: small nods of the head and a hand that moves with the words",
                        length=1.6, loop=True, keys={
                            "HEAD": [_k(0), _k(0.3, 6), _k(0.6, -2), _k(1.0, 5, 4), _k(1.3, 0, -3), _k(1.6)],
                            "RIGHT_ARM": [_k(0, -10), _k(0.5, -28, 0, 6), _k(1.0, -18, 0, 2), _k(1.6, -10)],
                        }),
        "listen": Gesture(description="listening: still, an occasional slight tilt of the head",
                          length=3.0, loop=True, keys={
                              "HEAD": [_k(0), _k(1.2, 3, 0, 4), _k(2.4, 2, 0, -2), _k(3.0)],
                          }),
        "nod": Gesture(description="a nod — yes", length=0.9, keys={
            "HEAD": [_k(0), _k(0.2, 20, ease="out"), _k(0.45, 2), _k(0.65, 14), _k(0.9)]}),
        "shake_head": Gesture(description="shaking the head — no", length=1.0, keys={
            "HEAD": [_k(0), _k(0.2, 0, -28), _k(0.45, 0, 26), _k(0.7, 0, -20), _k(1.0)]}),
        "wave": Gesture(description="waving a raised hand", length=1.6, keys={
            "RIGHT_ARM": [_k(0), _k(0.3, -160, 0, -15, "out"), _k(0.6, -160, 0, -45), _k(0.9, -160, 0, -15),
                          _k(1.2, -160, 0, -45), _k(1.6, ease="in")]}),
        "point": Gesture(description="pointing ahead with the right arm and holding it", length=1.6, keys={
            "RIGHT_ARM": [_k(0), _k(0.3, -90, 0, 0, "out"), _k(1.3, -90), _k(1.6)],
            "HEAD": [_k(0), _k(0.3, 0, -8), _k(1.6)]}),
        "shrug": Gesture(description="a shrug — who knows", length=1.0, keys={
            "RIGHT_ARM": [_k(0), _k(0.3, -20, 0, 25, "out"), _k(0.7, -20, 0, 25), _k(1.0)],
            "LEFT_ARM": [_k(0), _k(0.3, -20, 0, -25, "out"), _k(0.7, -20, 0, -25), _k(1.0)],
            "HEAD": [_k(0), _k(0.3, -6, 0, 8), _k(0.7, -6, 0, 8), _k(1.0)]}),
        "facepalm": Gesture(description="a hand to the face — despair", length=1.8, keys={
            "RIGHT_ARM": [_k(0), _k(0.35, -135, -25, 0, "out"), _k(1.4, -135, -25, 0), _k(1.8)],
            "HEAD": [_k(0), _k(0.35, 22), _k(1.4, 22), _k(1.8)]}),
        "knock": Gesture(description="knocking on a door or a table", length=1.2, keys={
            "RIGHT_ARM": [_k(0), _k(0.2, -75), _k(0.35, -55, ease="in"), _k(0.5, -75), _k(0.65, -55, ease="in"),
                          _k(0.8, -75), _k(0.95, -55, ease="in"), _k(1.2)]}),
        "look_around": Gesture(description="glancing left and right", length=2.4, keys={
            "HEAD": [_k(0), _k(0.6, 0, 35), _k(1.2, 0, 35), _k(1.8, 0, -30), _k(2.4)]}),
        "write": Gesture(description="writing on a desk or a clipboard, head down", length=1.2, loop=True, keys={
            "HEAD": [_k(0, 30), _k(1.2, 30)],
            "RIGHT_ARM": [_k(0, -45, 0, 0), _k(0.3, -48, 8, 0), _k(0.6, -45, -6, 0), _k(0.9, -48, 6, 0), _k(1.2, -45)]}),
    },
)


def load(save_dir: Path | None) -> Library:
    """The built-in library, with the operator's own laid over it."""
    if save_dir is None:
        return BUILTIN
    path = Path(save_dir) / REGISTRY_DIR / LIBRARY_FILE
    if not path.is_file():
        return BUILTIN
    return BUILTIN.merged(Library.model_validate(json.loads(path.read_text(encoding="utf-8"))))


def pose_keys(pose: Pose, at: float, ease: str) -> dict[str, list[tuple[float, Rot, str]]]:
    """A pose as keys: every part it moves arrives at `at + settle`, and every part it
    does NOT move is sent back to rest, so a new pose never inherits a raised arm
    from the old one."""
    out: dict[str, list[tuple[float, Rot, str]]] = {}
    end = at + pose.settle
    for part in ("HEAD", "BODY", "RIGHT_ARM", "LEFT_ARM", "RIGHT_LEG", "LEFT_LEG"):
        rot = pose.parts.get(part, (0.0, 0.0, 0.0))
        out[part] = [(end, rot, ease)]
    for part, rot in pose.parts.items():
        out.setdefault(part, [(end, rot, ease)])
    return out


def gesture_keys(g: Gesture, at: float, until: float | None = None) -> dict[str, list[tuple[float, Rot, str]]]:
    """A gesture laid onto the take's clock from `at`, looped to `until` when it loops
    (a looped gesture with no end plays once), ending where it started so whatever
    pose holds takes over cleanly."""
    out: dict[str, list[tuple[float, Rot, str]]] = {}
    reps = 1
    if g.loop and until is not None and until > at:
        reps = max(1, int((until - at) // g.length))
    for part, keys in g.keys.items():
        seq = []
        for r in range(reps):
            base = at + r * g.length
            for k in keys:
                if r and k.t == 0:
                    continue  # the loop's seam: last key of one rep is the first of the next
                seq.append((base + k.t, k.rot, k.ease))
        out[part] = seq
    return out
