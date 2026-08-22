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

# `field` is aliased: Row has an attribute of that name, which would shadow it
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from .job import Entity, Scene, VideoJob

# Stages that can carry a breakpoint, in pipeline order. Drama has no idea stage —
# its premise is the input, not something the pipeline invents.
_INFO_STAGES = ["idea", "script", "tts", "footage", "subtitles", "assemble", "metadata"]
# drama drops idea and gains two of its own: `entities` (the visual registry) right
# after the script, and `cut` (the episode boundaries) right before anything is generated
_DRAMA_STAGES = (
    ["script", "entities", "tts", "cut"]
    + [s for s in _INFO_STAGES if s not in ("idea", "script", "tts")]
)


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
    # which part of the source item this row edits. A document may show several rows
    # per item (the script shows what is SAID and what is SHOWN); a HEAD_FIELDS row is
    # the one that defines the item's existence, the rest attach to the preceding one.
    field: str = "text"
    # how the row is edited: free text, a number, one of `options`, or a set of them
    # (`chips`). The value stays a string either way — comma-separated for chips — so
    # reading, applying and the AI edit line need not care.
    kind: str = "text"  # text | number | choice | chips
    options: list[str] = dc_field(default_factory=list)


@dataclass
class Doc:
    """Everything a breakpoint screen needs: the rows, plus what may be done to them."""

    stage: str
    rows: list[Row] = dc_field(default_factory=list)
    variable: bool = False  # operator may add/remove/reorder items
    # operator may add/remove/move the PART separators, i.e. re-cut the drama into a
    # different number of episodes. Separate from `variable`: the cut breakpoint lets
    # the boundaries move while the scenes themselves stay put.
    cuttable: bool = False
    subject: str = "lines"  # what the AI rewrite line is editing (goes into the prompt)
    note_key: str = ""  # i18n key of the hint shown under the list
    note_extra: str = ""  # run-specific hint appended to it (e.g. the cast roster)

    @property
    def editable(self) -> bool:
        return any(not r.readonly for r in self.rows)


@dataclass
class Group:
    """One reviewed item and every row that belongs to it. A scene is a group: what
    is said, what is shown, and who is in it are three rows of the SAME thing, so
    they move, and are dropped, together."""

    head: Row  # the "text" row; its existence is the item's existence
    extras: list[Row] = dc_field(default_factory=list)

    @property
    def rows(self) -> list[Row]:
        return [self.head] + self.extras


# The row field that opens a new item. Every document names its head after what the
# item actually IS — a scene is its spoken "text", a registry entry is its "name" —
# because the TUI labels each row from its field, and a registry entry headed "text"
# would be captioned "voiceover".
HEAD_FIELDS = frozenset({"text", "name", "part"})

# The field of a part separator: the marker that one episode ends here and the next
# begins. It is a head field, so a separator is an item of its own — which is what
# makes the existing move/add/drop machinery re-cut the drama for free.
PART_FIELD = "part"


def part_row(number: int) -> Row:
    """A separator opening episode `number`.

    Its value is never edited: which episode a scene belongs to is decided by WHERE
    the separator sits, not by a number typed into it, and renumbering after a move
    is the pipeline's job (see :func:`..parts.renumber`). What the operator does to
    it is move it, add one, or drop one — and dropping one merges two episodes.
    """
    return Row(label="bp.f.part", value=str(number), field=PART_FIELD, readonly=True)


def with_part_rows(rows: list[Row], scenes: list[Scene], *, always: bool) -> list[Row]:
    """Interleave separators into a per-scene document, one opening each episode.

    `rows` must be in scene order; a scene's rows are found by their ``src``. With
    ``always`` false the separators only appear once the drama really is split —
    there is nothing to show a reader about a boundary that does not exist. The
    editable documents pass true, because being able to CREATE the first boundary is
    the point of them.
    """
    labels = [int(s.part or 1) for s in scenes]
    if not labels or (not always and len(set(labels)) < 2):
        return rows
    out: list[Row] = []
    seen: set[int] = set()
    for row in rows:
        label = labels[row.src] if row.src is not None and row.src < len(labels) else None
        if label is not None and label not in seen:
            seen.add(label)
            out.append(part_row(len(seen)))
        out.append(row)
    return out


def parts_from_rows(rows: list[Row]) -> list[int]:
    """Read the episode each item belongs to back off the separators' positions.

    Returns one number per non-separator item, in order. Items before the first
    separator belong to episode 1: a document may legitimately open with a scene
    (nothing has been cut yet), and an episode with no scenes is not an episode.
    """
    out: list[int] = []
    current = 1
    started = False
    for group in group_rows(rows):
        if group.head.field == PART_FIELD:
            current = current + 1 if started else 1
            started = True
            continue
        started = True
        out.append(current)
    return out


def group_rows(rows: list[Row]) -> list[Group]:
    """Split a flat row list into its items. Rows following a head row attach to it;
    a document with one field per item yields one row per group."""
    groups: list[Group] = []
    for row in rows:
        if row.field in HEAD_FIELDS or not groups:
            groups.append(Group(head=row))
        else:
            groups[-1].extras.append(row)
    return groups


def flatten(groups: list[Group]) -> list[Row]:
    return [row for g in groups for row in g.rows]


def move_group(rows: list[Row], index: int, delta: int) -> list[Row]:
    """Swap item `index` with its neighbour, carrying all of its rows along.
    Returns the new flat row list (unchanged when the move runs off the end)."""
    groups = group_rows(rows)
    target = index + delta
    if not (0 <= index < len(groups) and 0 <= target < len(groups)):
        return rows
    groups[index], groups[target] = groups[target], groups[index]
    return flatten(groups)


# -- reading a job into rows -----------------------------------------------


def generator_names() -> list[str]:
    """Every generator a scene may be pinned to (same names the orchestration uses)."""
    from ..media.generate import PHOTO_MODELS, VIDEO_MODELS

    return list(VIDEO_MODELS) + list(PHOTO_MODELS)


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
    """The script as written, not just what is spoken: every scene shows its
    narration AND what will be put on screen for it. The visual half is only
    editable here — by the footage breakpoint the clips already exist (and in the
    user-assisted flow the operator has already made them by hand)."""
    rows: list[Row] = []
    for i, s in enumerate(job.scenes):
        label = _scene_label(i, s)
        rows.append(Row(
            label=label, value=s.text, src=i, field="text",
            info=", ".join(s.characters) if mode == "drama" else "",
        ))
        if mode == "drama":
            rows.append(Row(label=label, value=s.video_prompt, src=i, field="prompt"))
            rows.append(Row(
                label=label, value=", ".join(s.characters), src=i, field="cast",
                kind="chips", options=list(job.cast_prompts),
            ))
            rows.append(Row(
                label=label, value=s.gen_model, src=i, field="model",
                kind="choice", options=generator_names(),
            ))
            rows.append(Row(
                label=label, value=f"{s.clip_target_s:g}" if s.clip_target_s else "",
                src=i, field="clip_s", kind="number",
            ))
        else:
            rows.append(Row(label=label, value=", ".join(s.keywords), src=i, field="keywords"))
    if mode == "drama":
        rows = with_part_rows(rows, job.scenes, always=True)
    return Doc(
        stage="script",
        rows=rows,
        variable=True,
        cuttable=mode == "drama",
        subject="a drama script: spoken narration lines and the English shot prompt of each scene"
        if mode == "drama" else "a voiceover script: spoken lines and each scene's stock-search keywords",
        note_key="bp.note.script",
        # the cast row only works with names spelled as the run knows them — the
        # footage stage matches them to swap each name for that character's look
        note_extra=(", ".join(job.cast_prompts) if mode == "drama" and job.cast_prompts else ""),
    )


def _entities_doc(job: VideoJob, mode: str) -> Doc:
    """The visual registry: three rows per entry — what it is called in the shot
    prompts, what it is, and the descriptor the generator actually receives.

    The name row is the "text" one, so a group is an entity: emptying the name drops
    the entry, and the document is variable, so an entity the model missed can be
    typed in by hand. Editing a `visual_prompt` here restyles every shot that names
    it at once, which is the whole point of the stage."""
    rows: list[Row] = []
    for i, e in enumerate(job.entities):
        label = f"#{i + 1}"
        shots = sum(1 for s in job.scenes if e.name.casefold() in (s.video_prompt or "").casefold())
        rows.append(Row(label=label, value=e.name, src=i, field="name",
                        info=f"{e.kind} · {shots} shots" if e.kind else f"{shots} shots"))
        rows.append(Row(label=label, value=e.note, src=i, field="note"))
        rows.append(Row(label=label, value=e.visual_prompt, src=i, field="look"))
    return Doc(
        stage="entities",
        rows=rows,
        variable=True,
        subject="a registry of recurring things in a drama: each one's name as the shot prompts "
                "spell it, a short note, and the English visual descriptor fed to the generator",
        note_key="bp.note.entities",
        note_extra=", ".join(job.cast_prompts) if job.cast_prompts else "",
    )


def _tts_doc(job: VideoJob, mode: str) -> Doc:
    def info(s: Scene) -> str:
        secs = s.audio_src_duration or s.duration
        got = f"{secs:.1f}s" if secs else "—"
        # a line re-voiced at its own speed says so: the length alone would not tell
        # the operator why one card runs shorter than its neighbours
        if s.tts_rate is not None:
            got += f" · {s.tts_rate:+d}%"
        return f"{got} · {Path(s.audio).name}" if s.audio else got

    return Doc(
        stage="tts",
        rows=with_part_rows(_scene_rows(job, info), job.scenes, always=False),
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
        rows=with_part_rows(
            [
                Row(label=_scene_label(i, s), value=_footage_query(s, mode), src=i,
                    field="prompt" if mode == "drama" else "keywords", info=info(s))
                for i, s in enumerate(job.scenes)
            ],
            job.scenes, always=False,
        ),
        subject="visual prompts / search queries, one per scene",
        note_key="bp.note.footage",
    )


def _cut_doc(job: VideoJob, mode: str) -> Doc:
    """Where one episode ends and the next begins, over the finished voiceover.

    The scenes are here to be read, not rewritten — by this point they have been
    voiced, and this breakpoint exists for one decision only: the boundaries. Each
    line carries what it actually runs to, so the cut can be made on real minutes
    instead of the writer's estimate.
    """
    def info(s: Scene) -> str:
        secs = s.audio_src_duration or s.duration
        return f"{secs:.1f}s" if secs else "—"

    rows = [
        Row(label=_scene_label(i, s), value=s.text, src=i, info=info(s), readonly=True)
        for i, s in enumerate(job.scenes)
    ]
    return Doc(
        stage="cut",
        rows=with_part_rows(rows, job.scenes, always=True),
        cuttable=True,
        subject="",  # no AI edit line: this is a structural decision, not a rewrite
        note_key="bp.note.cut",
    )


def _part_label(job: VideoJob, part) -> str:
    """"#2" when the video really is a serial, else nothing to say."""
    return f"#{part.number}" if len(job.parts) > 1 else ""


def _subtitles_doc(job: VideoJob, mode: str) -> Doc:
    rows: list[Row] = []
    for i, part in enumerate(job.parts):
        if part.ass is None:
            continue
        try:
            text = Path(part.ass).read_text(encoding="utf-8")
        except OSError:
            continue
        rows.append(Row(
            label=_part_label(job, part) or Path(part.ass).name, value=text, src=i,
            info=f"{Path(part.ass).name} · {len(text.splitlines())} lines", path=Path(part.ass),
        ))
    # no AI edit line here on purpose: an .ass file is timing-critical and a model
    # rewriting it wholesale would silently mangle the cue times.
    return Doc(stage="subtitles", rows=rows, subject="", note_key="bp.note.subtitles")


def _assemble_doc(job: VideoJob, mode: str) -> Doc:
    """One row per episode — cut, or still waiting for its hand-made clips."""
    rows: list[Row] = []
    pending = set(job.pending_parts)
    for i, part in enumerate(job.parts):
        if part.file and Path(part.file).exists():
            info = f"{Path(part.file).stat().st_size / 1e6:.1f} MB"
        elif part.number in pending:
            info = "awaiting clips"
        else:
            info = "missing"
        rows.append(Row(label=_part_label(job, part) or "#1", value=str(part.file or ""),
                        src=i, readonly=True, info=info))
    return Doc(stage="assemble", rows=rows, note_key="bp.note.assemble")


_META_FIELDS = ("title", "description", "tags")


def _meta_slots(job: VideoJob) -> list[tuple[int, str]]:
    """(part index, field) in document order — a row's ``src`` is a position in here.

    Metadata is written per episode, so the flat row list the breakpoint edits has to
    say which episode each field belongs to; the position does that, and both reading
    and writing walk the same list."""
    return [(i, key) for i in range(len(job.parts)) for key in _META_FIELDS]


def _metadata_doc(job: VideoJob, mode: str) -> Doc:
    rows: list[Row] = []
    multi = len(job.parts) > 1
    shown: set[int] = set()
    for src, (pi, key) in enumerate(_meta_slots(job)):
        if multi and pi not in shown:
            shown.add(pi)
            rows.append(part_row(job.parts[pi].number))
        raw = (job.parts[pi].metadata or {}).get(key, "")
        value = ", ".join(str(x) for x in raw) if isinstance(raw, list) else str(raw)
        rows.append(Row(label=f"bp.f.{key}", value=value, src=src))
    return Doc(stage="metadata", rows=rows, subject="video title, description and tags",
               note_key="bp.note.metadata")


_READERS = {
    "idea": _idea_doc,
    "script": _script_doc,
    "entities": _entities_doc,
    "tts": _tts_doc,
    "cut": _cut_doc,
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
    re-synthesizes exactly those. Returns True when anything actually changed —
    reordering counts, because the stage is what lays the lines out on the timeline
    (their absolute word timings would otherwise still describe the old order)."""
    old = job.scenes
    out: list[Scene] = []
    rows = [r for r in rows if r.field != PART_FIELD]  # separators are read-only here
    changed = [r.src for r in rows] != list(range(len(old)))
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
    """Rebuild the scenes from the multi-row script document. A "text" row opens a
    scene; the rows after it (prompt / keywords) belong to that same scene, so an
    operator-added line becomes a new scene with an empty visual — which the AI edit
    line or the footage stage's fallback then fills."""
    old = job.scenes
    out: list[Scene] = []
    labels = parts_from_rows(rows)  # where the separators now sit
    for n, group in enumerate(g for g in group_rows(rows) if g.head.field != PART_FIELD):
        head = group.head
        extras = {r.field: r for r in group.extras}
        text = head.value.strip()
        if not text:  # emptied narration drops the whole scene, visuals included
            continue
        src = old[head.src] if head.src is not None and head.src < len(old) else None
        scene = src.model_copy(deep=True) if src else _inherit(out[-1] if out else (old[0] if old else None))
        scene.text = text
        if "prompt" in extras:
            scene.video_prompt = extras["prompt"].value.strip()
        if "keywords" in extras:
            scene.keywords = [k.strip() for k in extras["keywords"].value.split(",") if k.strip()]
        if "cast" in extras:
            scene.characters = [c.strip() for c in extras["cast"].value.split(",") if c.strip()]
        if "model" in extras and extras["model"].value.strip():
            scene.gen_model = extras["model"].value.strip()
        # an EMPTY length means "as it was" — a scene the AI or the operator added
        # carries no number yet, and reading that as 0 would strip the clip length it
        # just inherited from its neighbour
        if extras.get("clip_s") and extras["clip_s"].value.strip():
            try:
                scene.clip_target_s = max(float(extras["clip_s"].value), 0.0)
            except ValueError:
                pass
        scene.part = labels[n] if n < len(labels) else 1
        out.append(scene)
    if out:
        job.scenes = out
    return False  # re-running the writer would discard exactly these edits


def _apply_cut(job: VideoJob, rows: list[Row], mode: str) -> bool:
    """Move the episode boundaries. The scenes themselves are read-only here, so all
    that is folded back is which episode each one now belongs to.

    Returns True: the stage has to run again, and that is the cheap half of the point
    — `cut` is what renumbers the labels and rebuilds the episode list from them."""
    scenes = [g.head for g in group_rows(rows) if g.head.field != PART_FIELD]
    labels = parts_from_rows(rows)
    before = [int(s.part or 1) for s in job.scenes]
    for row, label in zip(scenes, labels):
        if row.src is not None and row.src < len(job.scenes):
            job.scenes[row.src].part = label
    return [int(s.part or 1) for s in job.scenes] != before


def _apply_entities(job: VideoJob, rows: list[Row], mode: str) -> bool:
    """Rebuild the registry from the edited rows. An entity whose name row is empty
    is dropped; a group with no source is one the operator added by hand."""
    old = job.entities
    out: list[Entity] = []
    for group in group_rows(rows):
        name = group.head.value.strip()
        if not name:  # an emptied name drops the entry, descriptor and all
            continue
        extras = {r.field: r.value.strip() for r in group.extras}
        src = old[group.head.src] if group.head.src is not None and group.head.src < len(old) else None
        entity = src.model_copy(deep=True) if src else Entity(name=name)
        entity.name = name
        if "note" in extras:
            entity.note = extras["note"]
        if "look" in extras:
            entity.visual_prompt = extras["look"]
        out.append(entity)
    job.entities = out
    return False  # re-running the stage would discard exactly these edits


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
    slots = _meta_slots(job)
    for row in rows:
        if row.src is None or row.src >= len(slots):
            continue
        pi, key = slots[row.src]
        meta = dict(job.parts[pi].metadata or {})
        if key == "tags":
            meta[key] = [t.strip() for t in row.value.split(",") if t.strip()]
        else:
            meta[key] = row.value.strip()
        job.parts[pi].metadata = meta
    return False  # publish reads the part, not a re-run of the stage


_WRITERS = {
    "idea": _apply_idea,
    "script": _apply_script,
    "entities": _apply_entities,
    "tts": _apply_tts,
    "cut": _apply_cut,
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
