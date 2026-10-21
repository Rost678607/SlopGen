"""Machinima blocks on a fandom video's picture track: where they are, and getting
their clips made.

A block is claimed by the picture matcher over a run of consecutive stretches
(`claim`), lives on the job as words rather than seconds (`measure` turns them into
seconds whenever the voice has moved), is directed by a model the first time
(`direct`), and is played and filmed in the game (`shoot`). Its shots show its clip
rather than a card (`framebase.apply_to_scenes`).

The game is the operator's to start. A run that reaches a block with the game closed
does not fail: it pauses, the way a run waiting on a picture does, and goes on once
the game answers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..pipeline.job import FrameShot, MachinimaBlock, VideoJob
from ..pipeline.manual import ManualInputPending
from . import film, library
from .anchors import Clock, stale_words, timing_hash
from .client import DEFAULT_PORT, Game, GameUnavailable
from .compile import compile_block
from .direction import Direction, validate
from .registry import Registry

log = logging.getLogger(__name__)

BLOCK_DIR = "machinima"


class MachinimaWaiting(ManualInputPending):
    """The run needs the game and the game is not there. Not a failure: start
    Minecraft with the fandom's world open, and resume."""

    def __init__(self, workdir: Path, pending: int, total: int, why: str):
        self.workdir = Path(workdir)
        self.pending, self.total = pending, total
        Exception.__init__(self, f"{pending}/{total} machinima blocks are waiting on the game — {why}")


# --------------------------------------------------------------------------
# where blocks are
# --------------------------------------------------------------------------


def claim(job: VideoJob, rows: list[dict]) -> list[MachinimaBlock]:
    """Blocks from the matcher's `machinima` rows. A row's stretches are split into
    runs of consecutive ones — a block is one continuous piece of the video — and a
    stretch already in a block (or claimed twice) is not taken again."""
    taken = {i for i, s in enumerate(job.frame_shots) if s.block}
    out = []
    n = len(job.machinima)
    for row in rows:
        idx = []
        for v in row.get("shots", []):
            try:
                i = int(v) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(job.frame_shots) and i not in taken:
                idx.append(i)
        for run in _runs(sorted(set(idx))):
            first, last = job.frame_shots[run[0]], job.frame_shots[run[-1]]
            nxt = job.frame_shots[run[-1] + 1] if run[-1] + 1 < len(job.frame_shots) else None
            b = MachinimaBlock(
                id=f"mc_{n:02d}",
                anchor_scene=first.anchor_scene, anchor_word=first.anchor_word,
                end_scene=nxt.anchor_scene if nxt else -1, end_word=nxt.anchor_word if nxt else -1,
                start=first.start, duration=last.start + last.duration - first.start,
                place=str(row.get("place", "")).strip(), why=str(row.get("why", "")).strip(),
            )
            n += 1
            out.append(b)
            for i in run:
                job.frame_shots[i].block = b.id
                taken.add(i)
    job.machinima += out
    return out


def _runs(idx: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for i in idx:
        if out and out[-1][-1] == i - 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def measure(job: VideoJob) -> None:
    """Seconds from words, after the clock has moved; and which shots each block now
    covers. A block whose anchor word is gone keeps its seconds — the montage room
    shows it, and the operator decides."""
    from ..pipeline import framebase

    cues, regions, total = framebase.timeline(job.scenes)
    where = {(c.scene, c.word): c.at for c in cues}
    for b in job.machinima:
        if b.failed:
            continue
        if (b.anchor_scene, b.anchor_word) in where:
            b.start = where[(b.anchor_scene, b.anchor_word)]
        end = where.get((b.end_scene, b.end_word))
        if end is None:
            end = min((r.end for r in regions if r.end > b.start), default=total)
        b.duration = max(0.0, end - b.start)
    live = [b for b in job.machinima if not b.failed]
    for s in job.frame_shots:
        s.block = next((b.id for b in live if b.start - 1e-6 <= s.start < b.start + b.duration - 1e-6), "")


def by_id(job: VideoJob, bid: str) -> MachinimaBlock | None:
    return next((b for b in job.machinima if b.id == bid), None)


def clip(job: VideoJob, b: MachinimaBlock) -> Path | None:
    """The clip a block shows: the final if there is one, else the draft."""
    for rel in (b.final, b.draft):
        if rel and (Path(job.workdir) / rel).is_file():
            return Path(job.workdir) / rel
    return None


def folder(job: VideoJob, b: MachinimaBlock) -> Path:
    return Path(job.workdir) / BLOCK_DIR / b.id


def load_direction(job: VideoJob, b: MachinimaBlock) -> Direction | None:
    if not b.direction:
        return None
    p = Path(job.workdir) / b.direction
    if not p.is_file():
        return None
    return Direction.model_validate(json.loads(p.read_text(encoding="utf-8")))


def save_direction(job: VideoJob, b: MachinimaBlock, d: Direction) -> Path:
    f = folder(job, b)
    f.mkdir(parents=True, exist_ok=True)
    p = f / "direction.json"
    p.write_text(json.dumps(d.model_dump(mode="json", by_alias=True, exclude_defaults=True),
                            ensure_ascii=False, indent=2), encoding="utf-8")
    b.direction = str(p.relative_to(job.workdir))
    return p


def words(job: VideoJob, b: MachinimaBlock, clock: Clock | None = None):
    clock = clock or Clock.of(job.scenes)
    return clock.span(b.start, b.start + b.duration)


# --------------------------------------------------------------------------
# getting clips
# --------------------------------------------------------------------------


def needs_shooting(job: VideoJob, b: MachinimaBlock, final: bool) -> bool:
    if b.failed:
        return False
    if b.stale:
        return True
    return clip(job, b) is None or (final and not b.final)


def mark_stale(job: VideoJob) -> None:
    """After a re-voice: a block whose PLAYED keys moved has to be played again, and one
    whose words were rewritten has to be redirected — the text no longer says what the
    direction was written for."""
    clock = Clock.of(job.scenes)
    for b in job.machinima:
        d = load_direction(job, b)
        if d is None or not b.timing:
            continue
        if stale_words(d, clock):
            b.stale = True
            b.issues.append("the narration under this block was rewritten — redirect it")
        elif timing_hash(d, clock, b.start) != b.timing:
            b.stale = True


def shoot(job: VideoJob, ctx, final: bool = True, port: int = DEFAULT_PORT,
          only: set[str] | None = None) -> None:
    """Direct what has no direction, then play, film and join every block that needs a
    clip. Pauses the run (`MachinimaWaiting`) when the game is not reachable."""
    todo = [b for b in job.machinima if needs_shooting(job, b, final) and (only is None or b.id in only)]
    if not todo:
        return
    fandom = ctx.store.fandoms.get(ctx.params.fandom)
    mc = getattr(fandom, "minecraft", None)
    try:
        game = Game(port=port, timeout=10.0).connect()
    except GameUnavailable as e:
        raise MachinimaWaiting(job.workdir, len(todo), len(job.machinima), str(e)) from e
    try:
        h = game.hello()
        if not h.get("in_world"):
            raise MachinimaWaiting(job.workdir, len(todo), len(job.machinima), "open the world in the game")
        if mc is not None and mc.world and h.get("world") != mc.world:
            raise MachinimaWaiting(job.workdir, len(todo), len(job.machinima),
                                   f"the game has {h.get('world')!r} open, this fandom is built in {mc.world!r}")
        reg = Registry.model_validate(game.call("registry.get"))
        save = Path(h["save_dir"]) if h.get("save_dir") else None
        lib = library.load(save)
        clock = Clock.of(job.scenes)

        undirected = [b for b in todo if load_direction(job, b) is None]
        if undirected:
            from .direct import direct_blocks

            direct_blocks(job, undirected, reg, lib, clock, ctx)

        for b in todo:
            if b.failed:
                continue
            try:
                _shoot_one(job, b, game, reg, lib, clock, final)
            except GameUnavailable as e:
                raise MachinimaWaiting(job.workdir, len(todo), len(job.machinima), str(e)) from e
            except Exception as e:  # one block's trouble is not the video's
                log.warning("machinima %s: %s", b.id, e)
                b.issues.append(f"filming failed: {e}")
    finally:
        game.close()


def _shoot_one(job: VideoJob, b: MachinimaBlock, game: Game, reg: Registry, lib, clock: Clock, final: bool) -> None:
    d = load_direction(job, b)
    if d is None:
        return
    errs = validate(d, reg, {(w.scene, w.i) for w in words(job, b, clock)}, set(lib.gestures), set(lib.poses))
    if errs:
        b.issues = errs
        return
    worlds = film.scan_places(game, reg, {sc.place for sc in d.scenes if sc.crowd})
    plans = compile_block(d, reg, lib, clock, b.start, b.duration, worlds=worlds)
    f = folder(job, b)
    f.mkdir(parents=True, exist_ok=True)
    jobs, issues = [], []
    for plan in plans:
        issues += [f"scene {plan.index + 1}: {w}" for w in plan.warnings]
        name = f"{b.id}_s{plan.index + 1}"
        take = film.perform(game, plan, (f / f"{name}.zip").resolve(), name)
        film.save_report(take, f / f"{name}.report.json")
        issues += [f"scene {plan.index + 1}: {i}" for i in take.issues]
        ks, cam_issues = film.keys(plan, take, reg)
        issues += [f"scene {plan.index + 1}: {i}" for i in cam_issues]
        kind = "final" if final else "draft"
        jobs.append(film.job(plan, take, ks, (f / f"{name}.{kind}.mp4").resolve(), final))
    results = film.render(game, jobs, final)
    clips = [Path(r["out"]) for r in results if r.get("ok")]
    issues += [f"render: {r.get('error', 'not written')}" for r in results if not r.get("ok")]
    b.issues = issues
    if len(clips) != len(jobs):
        return
    kind = "final" if final else "draft"
    out = film.join(clips, f / f"{kind}.mp4", b.duration)
    rel = str(out.relative_to(job.workdir))
    if final:
        b.final = rel
    else:
        b.draft = rel
    b.timing = timing_hash(d, clock, b.start)
    b.stale = False


def give_up(job: VideoJob, b: MachinimaBlock, why: str) -> list[FrameShot]:
    """A block that cannot be made: its stretches go back to the other sources."""
    b.failed = True
    b.issues.append(why)
    shots = [s for s in job.frame_shots if s.block == b.id]
    for s in shots:
        s.block = ""
    return shots
