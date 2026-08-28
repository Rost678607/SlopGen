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
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ..media.stock import IMAGE_EXTS

MANIFEST_NAME = "manual_shots.json"
# How a background shot's number may be written on a file the operator renamed by
# hand: `shot_03`, `shot3`, `shot-3`, `shot 03`. The voice inbox has been this
# forgiving about `scene_NN` from the start; there is no reason for the picture
# inbox to be stricter, and a file rejected over a hyphen looks like a bug.
_SHOT_RE = re.compile(r"shot[ _-]?(\d+)", re.IGNORECASE)
# the same for a foreground insert: `fg_03_1`, `fg3-1`, `fg 03 1`
_FG_RE = re.compile(r"fg[ _-]?(\d+)[ _-](\d+)", re.IGNORECASE)

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


def medium_of(source: str) -> str:
    """What a visuals source delivers — "photo", "video", or "" when the name does
    not say. The layer sources are named `<where>_<what>` (`stock_video`, `ai_photo`,
    `local_photo`), which is the whole rule."""
    return ("photo" if source.endswith("_photo")
            else "video" if source.endswith("_video") else "")


# What a delivered file turns out to be, asked of ffprobe rather than of its name —
# ("video" | "photo" | "", seconds). Nothing here cares what the shot ASKED for: a
# still is held and panned to length, a clip is fitted to it, and both work wherever
# the other does, so the only question is which of the two arrived.
#
# By content and not by extension, because the extension is wrong often enough to
# matter. An animated .gif is a clip that every extension table in this repository
# would have called a picture; an .mkv is a clip that none of them listed; a photo
# saved as .avif or a phone's .heic is a picture that none of them listed either. The
# tell is in the container: ffmpeg demuxes a single image through an image demuxer,
# and anything with a timeline reports a real format and a duration.
#
# Which image demuxer, though, is not fixed. A file handed over by PATH can go to
# `image2` instead of `jpeg_pipe` — that is what happens to a JPEG whose header the
# stricter jpeg probe will not vouch for, which covers a good share of what an image
# generator or a stock site hands back — and `image2`, unlike the pipe demuxers,
# invents a one-frame duration (0.04 s at the nominal 25 fps). Reading that as a
# timeline is how a still ends up cut as a 2-frame background. So a lone frame counts
# as a still whatever the container is called, and the name test lists `image2` too.
_STILL_FORMATS = {"image2", "image2pipe"}
_PROBED: dict[tuple, tuple[str, float]] = {}


def _is_still(format_name: str, stream: dict) -> bool:
    """True when the picture ffprobe found has no timeline: an image demuxer, or a
    single frame in a container that does have one."""
    names = {n for n in str(format_name).split(",") if n}
    if any(n.endswith("_pipe") for n in names) or names & _STILL_FORMATS:
        return True
    try:
        return int(stream.get("nb_frames") or 0) == 1
    except (TypeError, ValueError):
        return False


def probe_asset(path: Path) -> tuple[str, float]:
    """``("video", seconds)``, ``("photo", 0.0)``, or ``("", 0.0)`` when there is no
    picture in the file at all (an audio track, a half-copied download, a PDF)."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return "", 0.0
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _PROBED:
        return _PROBED[key]
    answer = ("", 0.0)
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_type,nb_frames:format=format_name,duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout or "{}")
        fmt = data.get("format") or {}
        picture = next((s for s in data.get("streams") or []
                        if s.get("codec_type") == "video"), None)
        if picture is not None:
            if _is_still(fmt.get("format_name", ""), picture):
                answer = "photo", 0.0
            else:
                try:
                    seconds = float(fmt.get("duration") or 0.0)
                except (TypeError, ValueError):
                    seconds = 0.0
                answer = ("video", seconds) if seconds > 0 else ("", 0.0)
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        answer = "", 0.0
    _PROBED[key] = answer
    return answer


def is_photo_file(path: Path) -> bool:
    """True when the delivered file is a still. Falls back to the extension only for
    a file ffprobe would not answer about, so a caller always gets a straight yes/no
    for something already accepted."""
    kind, _s = probe_asset(path)
    return kind == "photo" if kind else Path(path).suffix.lower() in IMAGE_EXTS


def _valid_asset(path: Path) -> bool:
    """A dropped file counts if there is a picture in it — moving or not."""
    return probe_asset(path)[0] != ""


def _match_shot(shots: list[ManualShot], stem: str) -> ManualShot | None:
    """Find the shot an inbox file belongs to. Primary match is an exact id
    (``shot_03``, ``fg_03_1``); as a convenience ``shot_3`` also maps to the
    background shot ``shot_03``."""
    exact = next((s for s in shots if s.id == stem), None)
    if exact is not None:
        return exact
    m = _FG_RE.fullmatch(stem)
    if m:
        wanted = f"fg_{int(m.group(1)):02d}_{int(m.group(2))}"
        return next((s for s in shots if s.id == wanted), None)
    m = _SHOT_RE.fullmatch(stem)
    if m:
        wanted = f"shot_{int(m.group(1)):02d}"
        return next((s for s in shots if s.id == wanted), None)
    return None


# Files an inbox may hold that are nobody's delivery and should not be complained
# about: the operator's own notes, and the half-written files a browser leaves behind.
_IGNORED_SUFFIXES = {".txt", ".md", ".json", ".part", ".crdownload", ".tmp", ".download"}


def scan_inbox(manifest: ManualManifest, workdir: Path) -> int:
    """Attach any inbox/<shot-id>.* files to still-undelivered shots. Returns the
    number newly delivered. Validation-only; normalization happens at assemble.

    No extension list: what a file IS gets asked of ffprobe (see :func:`probe_asset`),
    so an .mkv, an animated .gif or a phone's .heic is taken if there is a picture in
    it, and a file with no picture is left where it lies. Whichever kind arrives is
    the kind the shot becomes — a still where a clip was asked for is held and panned,
    a clip where a still was asked for is fitted to the beat."""
    inbox = inbox_dir(workdir)
    if not inbox.is_dir():
        return 0
    delivered = 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        shot = _match_shot(manifest.shots, f.stem)
        if shot is None or shot.status == "delivered":
            continue
        if _valid_asset(f):
            attach(shot, f)
            delivered += 1
    return delivered


def rejected_in_inbox(manifest: ManualManifest, workdir: Path) -> list[tuple[Path, str]]:
    """Every file sitting in the inbox that did NOT become a delivery, and why.

    Silence is the bug this exists for: a file named for a shot that nobody could
    read, or named for no shot at all, simply stayed on disk while the screen went on
    saying 0/11 — and the operator has no way to tell "not picked up yet" from "will
    never be picked up". Reasons are ids, translated by whoever displays them."""
    inbox = inbox_dir(workdir)
    if not inbox.is_dir():
        return []
    out: list[tuple[Path, str]] = []
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.name.startswith(".") or f.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        shot = _match_shot(manifest.shots, f.stem)
        if shot is None:
            out.append((f, "unknown_shot"))
        elif shot.clip and Path(shot.clip) == f:
            continue  # this is the delivery
        elif not _valid_asset(f):
            out.append((f, "no_picture"))
        elif shot.status == "delivered":
            out.append((f, "already_delivered"))
    return out


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
