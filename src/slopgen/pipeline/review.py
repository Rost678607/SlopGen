"""Breakpoints: park a run right after a chosen stage so the operator can see —
and edit — what that stage produced before the pipeline moves on.

The operator picks the stages on the wizard's Summary step (``RunParams.breakpoints``).
When the orchestrator finishes such a stage it writes a ``review`` checkpoint instead
of walking on, exactly the way :mod:`.manual` parks a job waiting for hand-made clips.
A breakpoint fires **once per video**: the stage is remembered in the checkpoint's
``reviewed`` list, so resuming after a review never re-parks on the same stage.

What a breakpoint shows is a :class:`Doc` — a flat list of :class:`Row` text lines,
because that is what every stage's output boils down to for review purposes: the
topic, the scene texts, the narration lines, the shot prompts, the metadata fields.
The TUI renders the rows, the operator (or the AI rewrite line) edits them, and
:func:`apply` folds them back into the :class:`~.job.VideoJob`.

``apply`` returns whether the edit invalidated the stage's own output — editing a
narration line at the ``tts`` breakpoint makes its audio stale, so that stage must
run again (cheaply: :mod:`.stages.tts` re-synthesizes only the lines that changed).
Editing the *script* invalidates nothing: re-running the writer would just throw the
operator's edits away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .job import Scene, VideoJob

# Stages that can carry a breakpoint, in pipeline order. Drama has no idea stage —
# its premise is the input, not something the pipeline invents.
_INFO_STAGES = ["idea", "script", "tts", "footage", "subtitles", "assemble", "metadata"]
_DRAMA_STAGES = [s for s in _INFO_STAGES if s != "idea"]


def available(mode: str) -> list[str]:
    """Stage names that may hold a breakpoint in this mode."""
    return list(_DRAMA_STAGES if mode == "drama" else _INFO_STAGES)


def wanted(breakpoints: list[str], mode: str) -> set[str]:
    """The requested breakpoints, filtered to the ones this mode actually runs."""
    return {b for b in breakpoints if b in available(mode)}


@dataclass
class Row:
    """One editable line of a breakpoint document.

    ``src`` is the index of the job item the row was read from — ``None`` marks a
    line the operator (or the AI) added, which is how :func:`apply` tells a rewrite
    from an insertion. ``path`` is set for rows that are backed by a file on disk.
    """

    label: str  # "#3", "#3 · AD", or a "bp.f.*" i18n key for named fields
    value: str
    src: int | None = None
    info: str = ""  # dim annotation: duration, generator, file name …
    readonly: bool = False
    path: Path | None = None


@dataclass
class Doc:
    """Everything a breakpoint screen needs: the rows, plus what may be done to them."""

    stage: str
    rows: list[Row] = field(default_factory=list)
    variable: bool = False  # operator may add/remove lines
    subject: str = "lines"  # what the AI rewrite line is editing (goes into the prompt)
    note_key: str = ""  # i18n key of the hint shown under the list

    @property
    def editable(self) -> bool:
        return any(not r.readonly for r in self.rows)


# -- reading a job into rows -----------------------------------------------


def _scene_label(i: int, scene: Scene) -> str:
    return f"#{i + 1} · AD" if scene.is_ad else f"#{i + 1}"


def _scene_rows(job: VideoJob, info) -> list[Row]:
    return [
        Row(label=_scene_label(i, s), value=s.text, src=i, info=info(s))
        for i, s in enumerate(job.scenes)
    ]


def _idea_doc(job: VideoJob, mode: str) -> Doc:
    return Doc(
        stage="idea",
        rows=[Row(label="bp.f.topic", value=job.topic, src=0)],
        subject="video topic",
        note_key="bp.note.idea",
    )


def _script_doc(job: VideoJob, mode: str) -> Doc:
    def info(s: Scene) -> str:
        if mode == "drama":
            return " · ".join(x for x in (", ".join(s.characters), s.gen_model) if x)
        return ", ".join(s.keywords)

    return Doc(
        stage="script",
        rows=_scene_rows(job, info),
        variable=True,
        subject="scene-by-scene voiceover script",
        note_key="bp.note.script",
    )


def _tts_doc(job: VideoJob, mode: str) -> Doc:
    def info(s: Scene) -> str:
        secs = s.audio_src_duration or s.duration
        got = f"{secs:.1f}s" if secs else "—"
        return f"{got} · {Path(s.audio).name}" if s.audio else got

    return Doc(
        stage="tts",
        rows=_scene_rows(job, info),
        variable=True,
        subject="spoken narration lines",
        note_key="bp.note.tts",
    )


def _footage_query(scene: Scene, mode: str) -> str:
    if mode == "drama":
        return scene.video_prompt
    return ", ".join(scene.visual_queries or scene.keywords)


def _footage_doc(job: VideoJob, mode: str) -> Doc:
    def info(s: Scene) -> str:
        if mode == "drama":
            return " · ".join(x for x in (s.gen_model, f"{s.clip_target_s:.0f}s" if s.clip_target_s else "") if x)
        assets = len(s.bg_assets) + len(s.fg_inserts)
        return f"{assets} assets · {s.duration:.1f}s" if assets else f"{s.duration:.1f}s"

    return Doc(
        stage="footage",
        rows=[
            Row(label=_scene_label(i, s), value=_footage_query(s, mode), src=i, info=info(s))
            for i, s in enumerate(job.scenes)
        ],
        subject="visual prompts / search queries, one per scene",
        note_key="bp.note.footage",
    )


def _ass_paths(job: VideoJob) -> list[Path]:
    return [Path(p) for p in (job.part_ass_paths or ([job.ass_path] if job.ass_path else []))]


def _subtitles_doc(job: VideoJob, mode: str) -> Doc:
    rows: list[Row] = []
    for i, p in enumerate(_ass_paths(job)):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        rows.append(Row(label=p.name, value=text, src=i, info=f"{len(text.splitlines())} lines", path=p))
    return Doc(stage="subtitles", rows=rows, subject="ASS subtitle files", note_key="bp.note.subtitles")


def _assemble_doc(job: VideoJob, mode: str) -> Doc:
    files = job.final_paths or ([job.final_path] if job.final_path else [])
    rows = [
        Row(label=f"#{i + 1}", value=str(p), src=i, readonly=True,
            info=f"{Path(p).stat().st_size / 1e6:.1f} MB" if Path(p).exists() else "missing")
        for i, p in enumerate(files)
    ]
    return Doc(stage="assemble", rows=rows, note_key="bp.note.assemble")


_META_FIELDS = ("title", "description", "tags")


def _metadata_doc(job: VideoJob, mode: str) -> Doc:
    meta = job.metadata or {}
    rows = []
    for i, key in enumerate(_META_FIELDS):
        raw = meta.get(key, "")
        value = ", ".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        rows.append(Row(label=f"bp.f.{key}", value=value, src=i))
    return Doc(stage="metadata", rows=rows, subject="video title, description and tags",
               note_key="bp.note.metadata")


_READERS = {
    "idea": _idea_doc,
    "script": _script_doc,
    "tts": _tts_doc,
    "footage": _footage_doc,
    "subtitles": _subtitles_doc,
    "assemble": _assemble_doc,
    "metadata": _metadata_doc,
}


def read(stage: str, job: VideoJob, mode: str) -> Doc:
    """The editable view of what `stage` left on the job."""
    reader = _READERS.get(stage)
    return reader(job, mode) if reader else Doc(stage=stage)


# -- folding edited rows back into the job ---------------------------------


def _clear_audio(scene: Scene) -> None:
    """Forget everything the TTS stage derived from the (now changed) text."""
    scene.audio = None
    scene.words = []
    scene.duration = 0.0
    scene.audio_src_duration = 0.0
    scene.audio_tempo = 1.0


def _inherit(prev: Scene | None) -> Scene:
    """A blank scene for an operator-added line. It copies the neighbour's slot
    assignment (generator, key, target length, part) so a line added to a drama
    script still has a shot to be rendered into."""
    if prev is None:
        return Scene(text="")
    return Scene(
        text="",
        part=prev.part,
        gen_model=prev.gen_model,
        key_mode=prev.key_mode,
        key=prev.key,
        clip_target_s=prev.clip_target_s,
    )


def _apply_scene_texts(job: VideoJob, rows: list[Row], *, resync: bool) -> bool:
    """Rebuild ``job.scenes`` from the edited lines, carrying over everything the
    source scene already held (keywords, cast, generator slot, audio). With
    ``resync`` the audio of every changed or added line is dropped so the TTS stage
    re-synthesizes exactly those. Returns True when anything actually changed."""
    old = job.scenes
    out: list[Scene] = []
    changed = False
    for row in rows:
        text = row.value.strip()
        if not text:  # an emptied line means "drop this scene"
            changed = True
            continue
        src = old[row.src] if row.src is not None and row.src < len(old) else None
        if src is None:
            scene = _inherit(out[-1] if out else (old[0] if old else None))
            dirty = True
        else:
            scene = src.model_copy(deep=True)
            dirty = src.text != text
        if dirty and resync:
            _clear_audio(scene)
        scene.text = text
        out.append(scene)
        changed = changed or dirty
    if not out:  # refuse to leave the job with nothing to say
        return False
    job.scenes = out
    return changed or len(out) != len(old)


def _apply_idea(job: VideoJob, rows: list[Row], mode: str) -> bool:
    topic = rows[0].value.strip() if rows else ""
    if topic:
        job.topic = topic
    return False  # nothing downstream has run yet


def _apply_script(job: VideoJob, rows: list[Row], mode: str) -> bool:
    _apply_scene_texts(job, rows, resync=False)
    return False  # re-running the writer would discard exactly these edits


def _apply_tts(job: VideoJob, rows: list[Row], mode: str) -> bool:
    return _apply_scene_texts(job, rows, resync=True)


def _apply_footage(job: VideoJob, rows: list[Row], mode: str) -> bool:
    changed = False
    for row in rows:
        if row.src is None or row.src >= len(job.scenes):
            continue
        scene = job.scenes[row.src]
        value = row.value.strip()
        if value == _footage_query(scene, mode):
            continue
        changed = True
        if mode == "drama":
            scene.video_prompt = value
        else:
            queries = [q.strip() for q in value.split(",") if q.strip()]
            # write back to whichever list the profile is actually reading
            if scene.visual_queries:
                scene.visual_queries = queries
            else:
                scene.keywords = queries
    return changed


def _apply_subtitles(job: VideoJob, rows: list[Row], mode: str) -> bool:
    for row in rows:
        if row.path is None:
            continue
        try:
            if row.path.read_text(encoding="utf-8") != row.value:
                row.path.write_text(row.value, encoding="utf-8")
        except OSError:
            continue
    return False  # the edited .ass files ARE the stage's output


def _apply_assemble(job: VideoJob, rows: list[Row], mode: str) -> bool:
    return False  # inspect-only


def _apply_metadata(job: VideoJob, rows: list[Row], mode: str) -> bool:
    meta = dict(job.metadata or {})
    for row in rows:
        if row.src is None or row.src >= len(_META_FIELDS):
            continue
        key = _META_FIELDS[row.src]
        if key == "tags":
            meta[key] = [t.strip() for t in row.value.split(",") if t.strip()]
        else:
            meta[key] = row.value.strip()
    job.metadata = meta
    return False  # publish reads the job, not a re-run of the stage


_WRITERS = {
    "idea": _apply_idea,
    "script": _apply_script,
    "tts": _apply_tts,
    "footage": _apply_footage,
    "subtitles": _apply_subtitles,
    "assemble": _apply_assemble,
    "metadata": _apply_metadata,
}


def apply(stage: str, job: VideoJob, rows: list[Row], mode: str) -> bool:
    """Fold the edited rows back into `job`. Returns True when the edit made the
    stage's own output stale, i.e. the stage has to run again."""
    writer = _WRITERS.get(stage)
    return bool(writer(job, rows, mode)) if writer else False
