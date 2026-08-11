"""The part — one publishable episode — as the pipeline's unit of finished work.

A drama shot on free generators is not made in one sitting: the operator hand-makes
the clips for episode 1 in some web tool, and episode 2 waits for tomorrow's quota.
So the tail of the chain (footage → subtitles → assemble → metadata, then publish)
does not run once over a whole job. It runs over the episodes whose clips have all
arrived, leaves the rest alone, and runs AGAIN on the next resume for whatever has
turned up since — while never redoing an episode it has already finished.

Two rules make that safe:

* **A part is its own timeline.** Its subtitles start at 0:00 and its scenes are cut
  into a file of their own, so nothing about episode 2 is needed to finish episode 1.
  (This is why there is no "offset of part N in the whole drama" here: there is no
  whole drama on the timeline, only episodes.)
* **A part-scoped stage is only *done* once every part has been through it.** Until
  then the orchestrator keeps it out of the checkpoint's completed list, so a resume
  walks back into it. :data:`PART_STAGES` names those stages.

How many parts there are is read off the scenes, not off ``params.parts``: the flag
is what was *asked* of the writer, while the labels on the scenes are what the script
actually says — and what the operator may re-cut at the ``script`` and ``cut``
breakpoints by dragging the separators about.
"""

from __future__ import annotations

from collections.abc import Iterable

from .job import Part, Scene, VideoJob

# The stages that work an episode at a time. They are contiguous and end at metadata:
# everything before them (the script, the registry, the voiceover) is written for the
# whole drama at once and cannot be split.
PART_STAGES = ("footage", "subtitles", "assemble", "metadata")


def requested_parts(params) -> int:
    """How many episodes the writer was ASKED for. Only the script stage cares: from
    then on the scene labels are the truth (see :func:`count`)."""
    if getattr(params, "mode", "info") != "drama":
        return 1
    return max(1, int(getattr(params, "parts", 1) or 1))


def count(scenes: Iterable[Scene]) -> int:
    """How many episodes the script actually has, per its scene labels."""
    return max((int(s.part or 1) for s in scenes), default=1)


def _assign_evenly(scenes: list[Scene], parts: int) -> None:
    n = len(scenes)
    for i, scene in enumerate(scenes):
        scene.part = min(parts, int(i * parts / max(n, 1)) + 1)


def normalize_scene_parts(scenes: list[Scene], parts: int) -> None:
    """Clamp/validate LLM-authored part labels.

    A valid script has monotonic part numbers and at least one scene in each
    requested part. If the model omitted labels or left gaps, fall back to an
    even split so assembly still produces deterministic files.
    """
    parts = max(1, int(parts or 1))
    if not scenes:
        return
    if parts == 1:
        for scene in scenes:
            scene.part = 1
        return

    for scene in scenes:
        scene.part = min(parts, max(1, int(scene.part or 1)))

    last = 1
    monotonic = True
    for scene in scenes:
        if scene.part < last:
            monotonic = False
            break
        last = scene.part

    labels = {scene.part for scene in scenes}
    if not monotonic or labels != set(range(1, parts + 1)):
        _assign_evenly(scenes, parts)


def renumber(scenes: list[Scene]) -> None:
    """Close the gaps a re-cut left behind, keeping the scenes' order.

    Dropping a separator at a breakpoint leaves labels like 1,1,3,3 — the episodes
    are still in the right order and still correctly grouped, they are just misnamed.
    Renumbering them 1,1,2,2 is what keeps ``part_02.mp4`` meaning "the second
    episode" rather than "whatever the third separator used to be".
    """
    seen: dict[int, int] = {}
    for scene in scenes:
        label = int(scene.part or 1)
        if label not in seen:
            seen[label] = len(seen) + 1
        scene.part = seen[label]


def scenes_by_part(scenes: Iterable[Scene], number: int) -> list[Scene]:
    """The scenes of one episode, in order."""
    return [s for s in scenes if int(s.part or 1) == number]


def sync(job: VideoJob) -> list[Part]:
    """Bring ``job.parts`` in line with the scene labels and return it.

    Idempotent, and it never throws away what an episode has already produced: a
    part that survives a re-cut keeps its file, subtitles, metadata and publication.
    Only parts that no longer have a single scene are dropped.
    """
    old = {p.number: p for p in job.parts}
    job.parts = [old.get(n) or Part(number=n) for n in range(1, count(job.scenes) + 1)]
    return job.parts


def ready(job: VideoJob) -> list[Part]:
    """The episodes whose hand-made clips are all in, so work may proceed on them."""
    pending = set(job.pending_parts)
    return [p for p in job.parts if p.number not in pending]


def pending_message(job: VideoJob) -> str:
    """The note left on a job parked between episodes, for the operator's eye."""
    cut = [str(p.number) for p in job.parts if p.file]
    left = [str(n) for n in sorted(job.pending_parts)]
    have = f"part(s) {', '.join(cut)} cut" if cut else "nothing cut yet"
    return (
        f"{have}; part(s) {', '.join(left)} still need hand-made clips — add them via "
        f"`slopgen gather` and the run picks up where it left off"
    )
