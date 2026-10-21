"""How a keyed value travels from one key to the next.

One table for every keyed thing in a machinima block — a body part's rotation in a
gesture, a pose giving way to another, a camera's distance or zoom — because the
operator chooses the interpolation per transition, and a choice that meant one thing
for an arm and another for a zoom would have to be learned twice.

`step` is the switch the operator asked for: interpolation OFF, the value holds and
then jumps. Everything else is a curve over the unit interval.
"""

from __future__ import annotations

from typing import Literal

Ease = Literal["step", "linear", "smooth", "in", "out", "overshoot"]
EASES: tuple[str, ...] = ("step", "linear", "smooth", "in", "out", "overshoot")

# How far `overshoot` carries past the target before settling — the usual "back"
# constant, which reads as a deliberate flourish rather than a wobble.
BACK = 1.70158

# Flashback names its own interpolations differently and has no overshoot. A camera
# key is therefore never handed an overshoot: `camera` bakes dense keys, and the
# nearest Flashback type is only what the operator sees when opening the replay.
FLASHBACK = {
    "step": "HOLD", "linear": "LINEAR", "smooth": "EASE_IN_OUT",
    "in": "EASE_IN", "out": "EASE_OUT", "overshoot": "EASE_OUT",
}


def ease(kind: str, t: float) -> float:
    """The eased fraction for a linear fraction `t` in [0, 1]. Unknown kinds are
    `smooth`: a typo should look reasonable, not freeze a gesture."""
    t = min(max(t, 0.0), 1.0)
    if kind == "step":
        return 1.0 if t >= 1.0 else 0.0
    if kind == "linear":
        return t
    if kind == "in":
        return t * t * t
    if kind == "out":
        u = 1.0 - t
        return 1.0 - u * u * u
    if kind == "overshoot":
        u = t - 1.0
        return 1.0 + (BACK + 1.0) * u * u * u + BACK * u * u
    # smooth: cubic in-out
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def lerp_angle(a: float, b: float, f: float) -> float:
    """Degrees, the short way round — a head turning from 170° to -170° turns 20°,
    not 340°."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return a + d * f


def sample(keys: list[tuple[float, float, str]], t: float, angle: bool = False) -> float:
    """The value of a keyed track at time `t`.

    `keys` are (time, value, ease) sorted by time, where the ease belongs to the
    transition that ARRIVES at that key — the operator sets "how do we get here" on
    the key they are standing on. Before the first key the first value holds, after
    the last the last one does."""
    if not keys:
        return 0.0
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0, _), (t1, v1, kind) in zip(keys, keys[1:]):
        if t <= t1:
            f = ease(kind, (t - t0) / (t1 - t0) if t1 > t0 else 1.0)
            return lerp_angle(v0, v1, f) if angle else lerp(v0, v1, f)
    return keys[-1][1]
