"""Stage 8: generate title/description/tags and append the ad link snippet."""

from __future__ import annotations

import json

from ..context import AppContext
from ..job import VideoJob
from .idea import LANG_NAMES

SYSTEM = (
    "You write metadata for viral vertical short videos. Respond with JSON only:\n"
    '{"title": "<max 90 chars, hooky, no clickbait-brackets spam>", '
    '"description": "<2-3 sentences>", "tags": ["<tag>", ...max 12], "hashtags": ["#tag", ...max 4]}'
)


def run(job: VideoJob, ctx: AppContext) -> None:
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    script_text = " ".join(s.text for s in job.scenes if not s.is_ad)
    user = f"Topic: {job.topic}\nScript: {script_text}\nWrite title/description/hashtags in {lang}; tags in English."
    meta = ctx.llm.complete_json("metadata", SYSTEM, user)

    hashtags = meta.get("hashtags", [])
    if "#Shorts" not in hashtags:
        hashtags.append("#Shorts")
    description = meta.get("description", "").strip()
    if ctx.ad and ctx.ad.description.snippet:
        description += "\n\n" + ctx.ad.description.snippet.format(url=ctx.ad.url)
    description += "\n\n" + " ".join(hashtags)

    job.metadata = {
        "title": meta["title"][:100],
        "description": description,
        "tags": meta.get("tags", [])[:15],
        "topic": job.topic,
        "lang": ctx.params.lang,
        "content_type": ctx.params.content_type,
        "duration_s": round(job.total_duration, 2),
    }
    (job.workdir / "metadata.json").write_text(
        json.dumps(job.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
