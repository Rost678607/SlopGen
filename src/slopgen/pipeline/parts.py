"""Helpers for multi-part AI-drama output."""

from __future__ import annotations

from collections.abc import Iterable

from .job import Scene


def requested_parts(params) -> int:
    """How many output files this run should produce for one job."""
    if getattr(params, "mode", "info") != "drama":
        return 1
    return max(1, int(getattr(params, "parts", 1) or 1))


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


def scenes_by_part(scenes: Iterable[Scene], parts: int) -> list[list[Scene]]:
    groups = [[] for _ in range(max(1, int(parts or 1)))]
    for scene in scenes:
        idx = min(len(groups), max(1, int(scene.part or 1))) - 1
        groups[idx].append(scene)
    return groups


def part_start_offsets(scenes: Iterable[Scene], parts: int) -> list[float]:
    starts = [0.0 for _ in range(max(1, int(parts or 1)))]
    seen = [False for _ in starts]
    offset = 0.0
    for scene in scenes:
        idx = min(len(starts), max(1, int(scene.part or 1))) - 1
        if not seen[idx]:
            starts[idx] = offset
            seen[idx] = True
        offset += scene.duration
    return starts
