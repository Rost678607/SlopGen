"""Stage 1: generate a video topic, avoiding recently used ones."""

from __future__ import annotations

from ..context import AppContext
from ..job import VideoJob

LANG_NAMES = {"en": "English", "ru": "Russian"}

SYSTEM = (
    "You invent topics for viral vertical short videos (YouTube Shorts). "
    'Respond with JSON only: {"topic": "<one topic, a single sentence>"}. '
    "The topic must be specific and hooky, not generic."
)


def run(job: VideoJob, ctx: AppContext) -> None:
    if ctx.params.idea.strip():
        job.topic = ctx.params.idea.strip()
        return
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    recent = [
        h["topic"]
        for h in ctx.load_history()[-30:]
        if h.get("content_type") == ctx.params.content_type and h.get("lang") == ctx.params.lang
    ]
    brief = ctx.content.idea_brief.get(ctx.params.lang) or next(iter(ctx.content.idea_brief.values()))
    user = f"Niche brief: {brief}\n\nWrite the topic in {lang}."
    if recent:
        user += "\n\nDo NOT repeat or paraphrase these already-used topics:\n- " + "\n- ".join(recent)
    job.topic = ctx.llm.complete_json("idea", SYSTEM, user)["topic"].strip()
