"""Filming a block: play each scene in the game, plan its camera on what happened,
render, and join the scenes into the block's clip.

The two passes are the shape of everything here. A scene is PLAYED first (`perform`):
the mod runs it in the live world, Flashback records it, and the report says where
every actor actually was on every tick. Only then is the camera PLANNED (`keys`),
because the camera search (see `camera`) needs those positions — an actor walks with
its own pathfinding, and the side of the pillar it passed is not known in advance.
Then the replays are RENDERED in one batch (`render`): opening a replay closes the
world, so the trip out and back is paid once, not per scene.

A draft is rendered without shaders at half size, for looking at while directing;
the final with shaders at full size. Both come from the same recording, so a camera
change re-renders without re-playing.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import camera as cam
from .compile import PREROLL, TPS, ScenePlan
from .registry import Camera, Line, Registry
from .voxels import World

log = logging.getLogger(__name__)

FPS = 30
KEY_EVERY = 2  # ticks between camera keys written to Flashback
PLAY_SLACK = 120.0  # seconds a take may run past its length before it counts as stuck
RENDER_SLACK = 900.0  # seconds a render batch may take per scene (shaders are slow)


@dataclass
class Take:
    """What one played scene left behind."""

    scene: int
    replay: str
    report: dict
    issues: list[str] = field(default_factory=list)

    def tracks(self) -> dict[str, cam.Track]:
        out = {}
        for alias, a in (self.report.get("actors") or {}).items():
            rows = a.get("track") or []
            bodies, last = [], None
            for r in rows:
                if r is None:
                    # not on stage yet (or gone): hold the nearest known spot so the
                    # camera search has something to stand on
                    bodies.append(last)
                    continue
                last = cam.Body((r[0], r[1], r[2]), r[3], r[6] if len(r) > 6 else cam.EYE)
                bodies.append(last)
            first = next((b for b in bodies if b is not None), None)
            if first is None:
                continue
            out[alias] = cam.Track([b or first for b in bodies])
        return out

    def world(self) -> World | None:
        w = self.report.get("world")
        return World.from_payload(w) if w else None

    def uuids(self) -> list[str]:
        return list((self.report.get("uuids") or {}).values())


def perform(game, plan: ScenePlan, out: Path, name: str) -> Take:
    """Play one scene and record it. Blocks until the world is back."""
    report = game.call("perform", {"script": plan.script, "record": True, "name": name,
                                   "out": str(out)},
                       timeout=plan.script["ticks"] / TPS + PLAY_SLACK)
    return Take(plan.index, report.get("replay") or "", report, list(report.get("issues") or []))


def keys(plan: ScenePlan, take: Take, reg: Registry, through_glass: bool = True) -> tuple[list[dict], list[str]]:
    """Camera keys for one scene, on the take's own clock (tick 0 = recording start),
    and whatever the search could not solve."""
    world = take.world()
    tracks = take.tracks()
    if world is None:
        return [], ["the take came back without a world scan — the camera cannot be planned"]
    place = reg.place(plan.script["place"])
    out: list[dict] = []
    issues: list[str] = []
    for n, shot in enumerate(plan.shots):
        spec = shot["camera"]
        t0 = shot["start"] + PREROLL
        dur = max(0.1, shot["end"] - shot["start"])
        for path in _shot_paths(spec, t0, dur, plan, tracks, world, reg, place, through_glass):
            issues += [f"shot {n + 1}: {i}" for i in path.issues]
            for s in path.samples:
                out.append({"tick": int(round((t0 + s.t) * TPS)), "pos": list(s.pos),
                            "yaw": s.yaw, "pitch": s.pitch, "fov": s.fov})
            if out:
                out[-1]["cut"] = True  # hold to the next shot: a cut, not a swoop
    # thin to one key every KEY_EVERY ticks, keeping every cut
    thinned, last = [], -10
    for k in sorted(out, key=lambda k: k["tick"]):
        if k.get("cut") or k["tick"] - last >= KEY_EVERY:
            if thinned and thinned[-1]["tick"] == k["tick"]:
                thinned[-1] = k
            else:
                thinned.append(k)
            last = k["tick"]
    return thinned, issues


def _shot_paths(spec, t0, dur, plan, tracks, world, reg, place, through_glass) -> list[cam.Path]:
    others = dict(tracks)
    scene = cam.Scene3D(world, others, through_glass)
    dist = _pair(spec.get("distance", 3.0))
    fov = _pair(spec.get("fov", 70.0))
    ease = spec.get("ease", "smooth")
    aim = spec.get("aim") or ""
    aim_track = tracks.get(aim)
    aim_point = None
    if aim and aim_track is None and place is not None:
        p = reg.find(place, aim)
        if p is not None and hasattr(p, "pos"):
            aim_point = (p.pos[0], p.pos[1] + 1.5, p.pos[2])
    if spec.get("on"):
        tr = tracks.get(spec["on"])
        if tr is None:
            return [cam.Path([], [cam.Issue(0, dur, f"{spec['on']} never appeared")])]
        return [cam.ride(tr, spec["on"], scene, duration=dur, side=spec.get("side", "front"),
                         distance=dist, height=spec.get("height", 0.0), orbit=spec.get("orbit", 90.0),
                         fov=fov, easing=ease, aim=aim_track, aim_name=aim, t0=t0)]
    if spec.get("style") == "dialogue":
        a, b = (spec.get("pair") or [None, None])[:2]
        out = []
        for seg_start, seg_end, speaker in _speakers(plan.turns, t0, t0 + dur, a, b):
            listener = b if speaker == a else a
            tl, ts = tracks.get(listener), tracks.get(speaker)
            if tl is None or ts is None:
                continue
            side = "shoulder_right" if len(out) % 2 == 0 else "shoulder_left"
            out.append(cam.ride(tl, listener, scene, duration=seg_end - seg_start, side=side,
                                distance=dist, fov=fov, easing=ease, aim=ts, aim_name=speaker, t0=seg_start))
            # ride() times its samples from its own t0; shift them onto the shot's
            for s in out[-1].samples:
                s.t += seg_start - t0
        return out
    if spec.get("fixed") and place is not None:
        c = reg.find(place, spec["fixed"], (Camera,))
        if c is None:
            return [cam.Path([], [cam.Issue(0, dur, f"no camera {spec['fixed']!r}")])]
        return [cam.fixed(tuple(c.pos), scene, duration=dur, aim=aim_track, aim_name=aim, aim_point=aim_point,
                          yaw=c.yaw, pitch=c.pitch, fov=fov if spec.get("fov") is not None else (c.fov, c.fov),
                          easing=ease, t0=t0)]
    if spec.get("rail") and place is not None:
        c = reg.find(place, spec["rail"], (Camera,))
        line = reg.by_id(c.rail) if c is not None else None
        if not isinstance(line, Line):
            return [cam.Path([], [cam.Issue(0, dur, f"rail camera {spec['rail']!r} has no rail")])]
        return [cam.rail(line.at, scene, duration=dur, travel=tuple(spec.get("travel", (0.0, 1.0))),
                         aim=aim_track, aim_name=aim, aim_point=aim_point, fov=fov, easing=ease, t0=t0)]
    return [cam.Path([], [cam.Issue(0, dur, "the shot names no camera")])]


def _speakers(turns: list[dict], start: float, end: float, a: str, b: str) -> list[tuple[float, float, str]]:
    """Who has the floor over [start, end), between `a` and `b` — one segment per turn,
    filled with `a` where nobody of the pair speaks."""
    segs = []
    for t in turns:
        s, e = max(start, t["start"]), min(end, t["end"])
        if e - s > 0.05 and t["speaker"] in (a, b):
            segs.append((s, e, t["speaker"]))
    if not segs:
        return [(start, end, a)]
    out, cursor = [], start
    for s, e, who in segs:
        if s > cursor + 0.05:
            out.append((cursor, s, out[-1][2] if out else a))
        out.append((max(s, cursor), e, who))
        cursor = e
    if end > cursor + 0.05:
        out.append((cursor, end, out[-1][2]))
    return out


def _pair(v) -> tuple[float, float]:
    if isinstance(v, (list, tuple)):
        return (float(v[0]), float(v[-1]))
    return (float(v), float(v))


def job(plan: ScenePlan, take: Take, keys_: list[dict], out: Path, final: bool,
        width: int = 1080, height: int = 1920) -> dict:
    """One render job for the mod: the scene's own window of the take, at the video's
    size for a final and half of it for a draft."""
    scale = 1 if final else 2
    return {"replay": take.replay, "out": str(out), "name": out.stem,
            "start_tick": int(plan.script["preroll"]), "end_tick": int(plan.script["ticks"]),
            "width": width // scale, "height": height // scale, "fps": FPS, "keys": keys_,
            "actors": take.uuids()}


def render(game, jobs: list[dict], final: bool) -> list[dict]:
    """Render a batch in one trip out of the world and back."""
    return game.call("render", {"jobs": jobs, "shaders": final},
                     timeout=RENDER_SLACK * max(1, len(jobs)))


def join(clips: list[Path], out: Path, length: float) -> Path:
    """The scenes of a block, one after another, cut to the block's length."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-t", f"{length:.3f}", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
                    str(out)], check=True)
    lst.unlink(missing_ok=True)
    return out


def save_report(take: Take, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(take.report, ensure_ascii=False), encoding="utf-8")
