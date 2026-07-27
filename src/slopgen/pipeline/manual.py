"""User-assisted ("manual") video generation: the shared core.

When a scene's generator is ``manual`` the clip is not produced automatically —
the operator generates it by hand in an external web tool (Kling / Veo / Pika,
hopping services and accounts to beat daily free limits) and hands the file back.

This module is the single source of truth for that flow, reused by both the drama
and info footage stages:

* A **manifest** (``<workdir>/manual/manual_shots.json``) lists every shot that
  needs a hand-made clip — its prompt, target length and delivery status. The
  manifest on disk is authoritative; the TUI gather screen is just a view over it.
* Prompts are also mirrored to ``<workdir>/manual/prompts/shot_NN.txt`` so they can
  be copied without the TUI.
* Finished clips are picked up from ``<workdir>/manual/inbox/`` (drop a file named
  ``shot_NN.<ext>``) or attached by path in the TUI — either way they land in the
  manifest as ``delivered``.

The footage stage calls :func:`collect_or_pause`. While any shot is undelivered it
raises :class:`ManualInputPending`, which the orchestrator turns into a clean
``paused`` checkpoint (not a failure) so the run resumes once the clips arrive.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ..media.ffmpeg import FFmpegError, duration_of
from ..media.stock import VIDEO_EXTS

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


@dataclass
class ShotSpec:
    """What a footage stage knows about one shot that needs a manual clip. The
    ``id`` is the stable identity (also the inbox filename stem): backgrounds use
    ``shot_NN``, foreground inserts ``fg_NN_K``. ``scene_index`` is kept for
    grouping/labels in the gather screen."""

    id: str
    prompt: str
    target_s: float
    scene_index: int = 0
    part: int = 1


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


class ManualInputPending(Exception):
    """Raised while a job still needs hand-made clips. Carries enough to point the
    operator at the manifest, prompts and inbox."""

    def __init__(self, workdir: Path, pending: int, total: int):
        self.workdir = Path(workdir)
        self.pending = pending
        self.total = total
        super().__init__(
            f"{pending}/{total} shots still need manual clips — add them via "
            f"`slopgen gather` or drop files into {inbox_dir(workdir)}"
        )


def _write_prompt_files(manifest: ManualManifest, workdir: Path) -> None:
    """Mirror each shot's prompt to manual/prompts/<shot-id>.txt for copy-outside-TUI."""
    pdir = prompts_dir(workdir)
    pdir.mkdir(parents=True, exist_ok=True)
    for shot in manifest.shots:
        (pdir / f"{shot.id}.txt").write_text(shot.prompt + "\n")


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
        )
        if prev and prev.status == "delivered" and prev.clip and Path(prev.clip).exists():
            shot.status = "delivered"
            shot.clip = prev.clip
        elif prev:
            shot.status = prev.status  # keep in_flight marker across resumes
        shots.append(shot)
    manifest = ManualManifest(shots=shots)
    manifest.save(workdir)
    _write_prompt_files(manifest, workdir)
    inbox_dir(workdir).mkdir(parents=True, exist_ok=True)
    return manifest


def _valid_clip(path: Path) -> bool:
    """A dropped file counts only if ffprobe can read a non-zero duration from it
    (guards against half-copied downloads / non-video files)."""
    try:
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
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
            continue
        shot = _match_shot(manifest.shots, f.stem)
        if shot is None or shot.status == "delivered":
            continue
        if _valid_clip(f):
            attach(shot, f)
            delivered += 1
    return delivered


def attach(shot: ManualShot, clip: Path) -> None:
    """Deliver one shot (one clip per shot — replaces whatever was there)."""
    shot.clip = Path(clip)
    shot.status = "delivered"


def collect_or_pause(
    workdir: Path, specs: list[ShotSpec], width: int, height: int
) -> dict[str, Path]:
    """The DRY entry both footage stages call. Build/refresh the manifest, pick up
    any inbox drops, and either return {shot_id: clip} once every shot is
    delivered, or raise ManualInputPending so the run parks in a `paused` state."""
    manifest = build_or_update(workdir, specs, width, height)
    if scan_inbox(manifest, workdir):
        manifest.save(workdir)
    if not manifest.all_delivered():
        raise ManualInputPending(workdir, len(manifest.pending()), len(manifest.shots))
    return manifest.delivered_map()
