"""Stage 1: generate a video topic, avoiding recently used ones."""

from __future__ import annotations

# LANG_NAMES lives with the prompt now; seven other stages import it from here, and
# this file stays its address.
from ...llm.topic import LANG_NAMES, write_topic  # noqa: F401
from ..context import AppContext
from ..job import VideoJob


def run(job: VideoJob, ctx: AppContext) -> None:
    if ctx.params.idea.strip():
        job.topic = ctx.params.idea.strip()
        return
    recent = [
        h["topic"]
        for h in ctx.load_history()[-30:]
        if h.get("content_type") == ctx.params.content_type and h.get("lang") == ctx.params.lang
    ]
    briefs = ctx.content.idea_brief
    # No content type ("auto") → no niche brief, let the model pick anything.
    brief = briefs.get(ctx.params.lang) or next(iter(briefs.values()), "")
    job.topic = write_topic(ctx.llm, ctx.params.lang, brief, recent)
