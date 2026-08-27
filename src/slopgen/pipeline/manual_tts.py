"""User-assisted VOICE: the operator supplies the audio for each line.

The same errand `manual.py` runs for footage, run for narration — and for the same
reason. Some of the best voices have no API at all: a web demo with a text box, a
studio product, a friend with a microphone, the operator reading it themselves. None
of those can be called from a stage, and all of them produce exactly what a stage
needs, which is a file.

So this is an ORTHOGONAL flag, not an engine (`--tts-source manual`): it does not
change what the voice is, only who fetches it. The shape is deliberately the one the
footage flow already established, because the operator has met it before —

* a **manifest** (``<workdir>/manual_voice/voice_lines.json``) lists every line and
  whether its audio has arrived;
* the lines are mirrored as plain text (``<workdir>/manual_voice/lines/scene_NN.txt``)
  so they can be read and copied without the TUI, and one ``script.txt`` holds the
  whole narration for pasting into a web demo in one go;
* audio is picked up from ``<workdir>/manual_voice/inbox/`` as ``scene_NN.<ext>``;
* until every line is in, :class:`ManualVoicePending` parks the run in a clean
  ``paused`` checkpoint — it inherits from `manual.ManualInputPending`, which the
  orchestrator already knows is not a failure.

Word timings come from the aligner, since a microphone emits no WordBoundary events.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel

from .manual import ManualInputPending

DIR_NAME = "manual_voice"
MANIFEST_NAME = "voice_lines.json"
SCRIPT_NAME = "script.txt"
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac", ".webm"}
# `scene_3.wav` is as good as `scene_03.wav` — the operator is renaming files by hand
_LINE_RE = re.compile(r"scene[_-]?(\d+)", re.IGNORECASE)


def voice_dir(workdir: Path) -> Path:
    return Path(workdir) / DIR_NAME


def inbox_dir(workdir: Path) -> Path:
    return voice_dir(workdir) / "inbox"


def lines_dir(workdir: Path) -> Path:
    return voice_dir(workdir) / "lines"


def manifest_path(workdir: Path) -> Path:
    return voice_dir(workdir) / MANIFEST_NAME


def line_id(index: int) -> str:
    """Also the inbox filename stem, so `scene_03.wav` needs no explaining."""
    return f"scene_{index:02d}"


class ManualLine(BaseModel):
    index: int
    text: str
    audio: Path | None = None

    @property
    def id(self) -> str:
        return line_id(self.index)

    @property
    def delivered(self) -> bool:
        return bool(self.audio and Path(self.audio).exists())


class ManualVoiceManifest(BaseModel):
    lines: list[ManualLine] = []

    @classmethod
    def load(cls, workdir: Path) -> "ManualVoiceManifest":
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

    def pending(self) -> list[ManualLine]:
        return [ln for ln in self.lines if not ln.delivered]

    def all_delivered(self) -> bool:
        return bool(self.lines) and not self.pending()

    def delivered_map(self) -> dict[int, Path]:
        return {ln.index: Path(ln.audio) for ln in self.lines if ln.delivered}


class ManualVoicePending(ManualInputPending):
    """Awaiting recordings. A subclass so the orchestrator's existing `paused`
    handling applies unchanged — this is a wait, never a failure."""

    def __init__(self, workdir: Path, pending: int, total: int):
        Exception.__init__(
            self,
            f"{pending}/{total} lines are still waiting on you — the text is in "
            f"{lines_dir(workdir)}, drop the audio into {inbox_dir(workdir)} as "
            f"scene_NN.wav, then resume",
        )
        self.workdir = Path(workdir)
        self.pending = pending
        self.total = total


def _write_line_files(manifest: ManualVoiceManifest, workdir: Path) -> None:
    """Mirror the lines to text: one file each for reading a line at a time, and one
    whole script for the demos that take a paragraph and hand back one file — the
    operator can then cut it up themselves, or voice it line by line."""
    d = lines_dir(workdir)
    d.mkdir(parents=True, exist_ok=True)
    for ln in manifest.lines:
        path = d / f"{ln.id}.txt"
        if not path.exists() or path.read_text(encoding="utf-8") != ln.text:
            path.write_text(ln.text, encoding="utf-8")
    (voice_dir(workdir) / SCRIPT_NAME).write_text(
        "\n\n".join(f"[{ln.id}]\n{ln.text}" for ln in manifest.lines), encoding="utf-8"
    )


def build_or_update(workdir: Path, texts: list[str]) -> ManualVoiceManifest:
    """Refresh the manifest against the script as it now stands. A line whose text
    was edited at a breakpoint loses its delivery — the recording no longer says what
    the script says — while every untouched line keeps the audio already supplied."""
    manifest = ManualVoiceManifest.load(workdir)
    by_index = {ln.index: ln for ln in manifest.lines}
    lines: list[ManualLine] = []
    for i, text in enumerate(texts):
        old = by_index.get(i)
        if old is not None and old.text == text:
            lines.append(old)
        else:
            lines.append(ManualLine(index=i, text=text))
    manifest.lines = lines
    _write_line_files(manifest, workdir)
    manifest.save(workdir)
    return manifest


def scan_inbox(manifest: ManualVoiceManifest, workdir: Path) -> int:
    """Attach inbox/scene_NN.* files to lines still without audio. Returns how many
    were newly delivered."""
    inbox = inbox_dir(workdir)
    if not inbox.is_dir():
        return 0
    by_id = {ln.id: ln for ln in manifest.lines}
    got = 0
    for f in sorted(inbox.iterdir()):
        if not f.is_file() or f.suffix.lower() not in AUDIO_EXTS:
            continue
        line = by_id.get(f.stem)
        if line is None:
            m = _LINE_RE.fullmatch(f.stem)
            line = by_id.get(line_id(int(m.group(1)))) if m else None
        if line is None or line.delivered:
            continue
        line.audio = f
        got += 1
    return got


def collect(workdir: Path, texts: list[str]) -> ManualVoiceManifest:
    manifest = build_or_update(workdir, texts)
    if scan_inbox(manifest, workdir):
        manifest.save(workdir)
    return manifest


def collect_or_pause(workdir: Path, texts: list[str]) -> dict[int, Path]:
    """{scene index: audio} once every line is in, else :class:`ManualVoicePending`.

    All-or-nothing even in drama mode, unlike footage: the voice comes before the
    picture, and an episode cannot be cut from lines that have not been said."""
    inbox_dir(workdir).mkdir(parents=True, exist_ok=True)
    manifest = collect(workdir, texts)
    if not manifest.all_delivered():
        raise ManualVoicePending(workdir, len(manifest.pending()), len(manifest.lines))
    return manifest.delivered_map()
