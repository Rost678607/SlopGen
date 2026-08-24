"""User-assisted footage: the shared core.

Some material does not come from an API. The operator supplies it, and there are two
quite different errands they may be running:

* **generate** — slopgen writes a prompt and they make the clip by hand in an external
  web tool (Kling / Veo / Pika, hopping services and accounts to beat daily free
  limits);
* **search** — slopgen writes what the shot needs plus ready-made queries, and they go
  and FIND existing footage or a photograph on a stock site (see ``llm/lookup``).

Everything after the instruction is identical, which is why both live here: the same
manifest, the same inbox, the same paused checkpoint, the same gather screen. Only
:data:`ShotKind` and what the task text says differ.

This module is the single source of truth for that flow, reused by both the drama
and info footage stages:

* A **manifest** (``<workdir>/manual/manual_shots.json``) lists every shot the
  operator owes — its task, target length and delivery status. The manifest on disk
  is authoritative; the TUI gather screen is just a view over it.
* Tasks are also mirrored to ``<workdir>/manual/prompts/shot_NN.txt`` so they can be
  read and copied without the TUI (see :func:`task_text`).
* Finished material is picked up from ``<workdir>/manual/inbox/`` (drop a file named
  ``shot_NN.<ext>``) or attached by path in the TUI — either way it lands in the
  manifest as ``delivered``. A still is as good as a clip: a search may legitimately
  come back with a photograph, and the file decides (:func:`is_photo_file`), not the
  source it was asked of.

Both footage stages call :func:`collect`; what they do about the shots still missing
differs. An info clip is one indivisible video, so :func:`collect_or_pause` waits for
every last shot and raises :class:`ManualInputPending` until then — which the
orchestrator turns into a clean ``paused`` checkpoint (not a failure) so the run
resumes once the clips arrive. A drama is published an episode at a time, so it asks
which *parts* are short (:meth:`ManualManifest.pending_parts`) and gets on with the
ones that are not; see :mod:`.parts`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..media.ffmpeg import FFmpegError, duration_of
from ..media.stock import IMAGE_EXTS, VIDEO_EXTS

MANIFEST_NAME = "manual_shots.json"
_SHOT_RE = re.compile(r"shot_(\d+)", re.IGNORECASE)

ShotStatus = Literal["pending", "in_flight", "delivered"]


def manual_dir(workdir: Path) -> Path:
    return Path(workdir) / "manual"


def inbox_dir(workdir: Path) -> Path:
    return manual_dir(workdir) / "inbox"


def prompts_dir(workdir: Path) -> Path:
    return manual_dir(workdir) / "prompts"


def manifest_path(workdir: Path) -> Path:
    return manual_dir(workdir) / MANIFEST_NAME


# What the operator is asked to do for one shot. Both end in the same place — a file
# in the inbox — but the instructions are opposite in kind, so the manifest says which:
#   generate: here is a prompt, make this clip in Kling/Veo/Pika
#   search:   here is what to find and some queries to find it with
ShotKind = Literal["generate", "search"]


@dataclass
class ShotSpec:
    """What a footage stage knows about one shot that needs a manual asset. The
    ``id`` is the stable identity (also the inbox filename stem): backgrounds use
    ``shot_NN``, foreground inserts ``fg_NN_K``. ``scene_index`` is kept for
    grouping/labels in the gather screen."""

    id: str
    prompt: str  # generate: the generator prompt · search: what to look for
    target_s: float
    scene_index: int = 0
    part: int = 1
    kind: ShotKind = "generate"
    # search only: ready-made queries for the stock sites, and whether a still or a
    # moving shot suits this beat (the writer of the queries decides — see llm/lookup)
    queries: list[str] = field(default_factory=list)
    want: Literal["photo", "video", ""] = ""


class ManualShot(BaseModel):
    id: str  # "shot_00" (background) or "fg_00_1" (foreground insert)
    scene_index: int = 0
    part: int = 1
    prompt: str = ""
    target_s: float = 0.0
    width: int = 1080
    height: int = 1920
    status: ShotStatus = "pending"
    clip: Path | None = None
    # -- user-assisted SEARCH (see ShotKind) --------------------------------
    kind: ShotKind = "generate"
    queries: list[str] = Field(default_factory=list)
    want: Literal["photo", "video", ""] = ""
    # set when the delivered file is a still rather than a clip; footage needs to
    # know because a photo has no length of its own and is held/panned instead
    photo: bool = False


class ManualManifest(BaseModel):
    shots: list[ManualShot] = []

    # -- persistence -------------------------------------------------------

    @classmethod
    def load(cls, workdir: Path) -> "ManualManifest":
        path = manifest_path(workdir)
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text())

    def save(self, workdir: Path) -> None:
        path = manifest_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(self.model_dump_json(indent=1))
        os.replace(tmp, path)

    # -- queries -----------------------------------------------------------

    def by_id(self, shot_id: str) -> ManualShot | None:
        return next((s for s in self.shots if s.id == shot_id), None)

    def pending(self) -> list[ManualShot]:
        return [s for s in self.shots if s.status != "delivered"]

    def all_delivered(self) -> bool:
        return bool(self.shots) and not self.pending()

    def delivered_map(self) -> dict[str, Path]:
        return {s.id: s.clip for s in self.shots if s.status == "delivered" and s.clip}

    def pending_parts(self) -> set[int]:
        """Episodes still short of a clip. An episode nobody has to hand-make a shot
        for never appears here, so it counts as ready the moment it is asked about."""
        return {int(s.part or 1) for s in self.pending()}

    def parts_ready(self) -> list[int]:
        """Episodes this manifest has every clip for — the ones worth resuming on."""
        pending = self.pending_parts()
        return sorted({int(s.part or 1) for s in self.shots} - pending)

    def shots_left(self, part: int) -> int:
        return sum(1 for s in self.pending() if int(s.part or 1) == part)


class ManualInputPending(Exception):
    """Raised while a job is still short of material the operator supplies — whether
    they are generating it or finding it. Carries enough to point them at the manifest,
    the task files and the inbox."""

    def __init__(self, workdir: Path, pending: int, total: int):
        self.workdir = Path(workdir)
        self.pending = pending
        self.total = total
        super().__init__(
            f"{pending}/{total} shots are still waiting on you — add them via "
            f"`slopgen gather` or drop files into {inbox_dir(workdir)}"
        )


def task_text(shot: ManualShot) -> str:
    """The whole job for one shot as plain text — what the prompt file holds and what
    the gather screen shows. A generation shot is its prompt and nothing else, because
    that is what gets pasted into Kling. A search shot cannot be one line: the operator
    needs to know what to look for, whether a still or a clip suits the moment, and
    what to actually type into a stock site, which is rarely the same words."""
    if shot.kind != "search":
        return shot.prompt
    lines = [shot.prompt]
    if shot.want:
        lines.append(f"[{shot.want}, ~{shot.target_s:.1f}s]")
    if shot.queries:
        lines.append("")
        lines.extend(shot.queries)
    return "\n".join(lines)


def _write_prompt_files(manifest: ManualManifest, workdir: Path) -> None:
    """Mirror each shot's task to manual/prompts/<shot-id>.txt for copy-outside-TUI."""
    pdir = prompts_dir(workdir)
    pdir.mkdir(parents=True, exist_ok=True)
    for shot in manifest.shots:
        (pdir / f"{shot.id}.txt").write_text(task_text(shot) + "\n")


def build_or_update(
    workdir: Path, specs: list[ShotSpec], width: int, height: int
) -> ManualManifest:
    """Create/refresh the manifest from the stage's shot specs, preserving any
    clips already delivered (matched by id). Returns the saved manifest."""
    old = ManualManifest.load(workdir)
    shots: list[ManualShot] = []
    for spec in specs:
        prev = old.by_id(spec.id)
        shot = ManualShot(
            id=spec.id,
            scene_index=spec.scene_index,
            part=spec.part,
            prompt=spec.prompt,
            target_s=spec.target_s,
            width=width,
            height=height,
            kind=spec.kind,
            queries=list(spec.queries),
            want=spec.want,
        )
        if prev and prev.status == "delivered" and prev.clip and Path(prev.clip).exists():
            shot.status = "delivered"
            shot.clip = prev.clip
            shot.photo = is_photo_file(prev.clip)
        elif prev:
            shot.status = prev.status  # keep in_flight marker across resumes
        shots.append(shot)
    manifest = ManualManifest(shots=shots)
    manifest.save(workdir)
    _write_prompt_files(manifest, workdir)
    inbox_dir(workdir).mkdir(parents=True, exist_ok=True)
    return manifest


def is_photo_file(path: Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def _valid_asset(path: Path) -> bool:
    """A dropped file counts only if it is usable. A clip must have a non-zero
    duration ffprobe can read (guards against half-copied downloads); a still only
    has to be a non-empty image — user-assisted SEARCH may legitimately deliver one,
    and a photo is held or panned to length rather than played."""
    try:
        if is_photo_file(path):
            return path.stat().st_size > 0
        return path.suffix.lower() in VIDEO_EXTS and duration_of(path) > 0
    except (FFmpegError, ValueError, KeyError, OSError):
        return False


def _match_shot(shots: list[ManualShot], stem: str) -> ManualShot | None:
    """Find the shot an inbox file belongs to. Primary match is an exact id
    (``shot_03``, ``fg_03_1``); as a convenience ``shot_3`` also maps to the
    background shot ``shot_03``."""
    exact = next((s for s in shots if s.id == stem), None)
    if exact is not None:
        return exact
    m = _SHOT_RE.fullmatch(stem)
    if m:
        want = f"shot_{int(m.group(1)):02d}"
        return next((s for s in shots if s.id == want), None)
    return None


def scan_inbox(manifest: ManualManifest, workdir: Path) -> int:
    """Attach any inbox/<shot-id>.* files to still-undelivered shots. Returns the
    number newly delivered. Validation-only; normalization happens at assemble."""
    inbox = inbox_dir(workdir)
    if not inbox.is_dir():
        return 0
    delivered = 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.suffix.lower() not in (VIDEO_EXTS | IMAGE_EXTS):
            continue
        shot = _match_shot(manifest.shots, f.stem)
        if shot is None or shot.status == "delivered":
            continue
        if _valid_asset(f):
            attach(shot, f)
            delivered += 1
    return delivered


def attach(shot: ManualShot, clip: Path) -> None:
    """Deliver one shot (one asset per shot — replaces whatever was there). The
    delivered file decides whether the shot is a still: a searching operator picks
    what the moment wants, and `want` was only ever a recommendation."""
    shot.clip = Path(clip)
    shot.photo = is_photo_file(clip)
    shot.status = "delivered"


def collect(
    workdir: Path, specs: list[ShotSpec], width: int, height: int
) -> ManualManifest:
    """The DRY entry both footage stages call: build/refresh the manifest from the
    stage's specs and pick up whatever has been dropped into the inbox since.

    What to do about the shots still missing is the caller's to decide, because the
    two modes want different answers — an info clip is one indivisible video and has
    to wait for all of them, while a drama can get on with the episodes that are
    ready (see :func:`pause_unless_delivered` and :mod:`.parts`)."""
    manifest = build_or_update(workdir, specs, width, height)
    if scan_inbox(manifest, workdir):
        manifest.save(workdir)
    return manifest


def pause_unless_delivered(workdir: Path, manifest: ManualManifest) -> dict[str, Path]:
    """All-or-nothing: {shot_id: clip} once every shot is in, else ManualInputPending
    so the orchestrator parks the run in a clean `paused` state."""
    if not manifest.all_delivered():
        raise ManualInputPending(workdir, len(manifest.pending()), len(manifest.shots))
    return manifest.delivered_map()


def collect_or_pause(
    workdir: Path, specs: list[ShotSpec], width: int, height: int
) -> dict[str, Path]:
    """Collect, and refuse to go on until every shot has a clip."""
    return pause_unless_delivered(workdir, collect(workdir, specs, width, height))
