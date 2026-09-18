"""Stage 8: generate title/description/tags and append the ad link snippet.

One set per part, because a part is one publishable video and each is uploaded on
its own — often days apart, once the operator has hand-made that episode's clips.
The writer is told which episode of how many it is describing and writes the marker
into the title itself, rather than having "Part 2/5" bolted on afterwards: where the
episode number belongs in a title is a matter of the language and the hook, and the
model is already writing in the content language.

And it is the one stage that a run has to ASK for (see :func:`wanted`). Everything
else in the chain makes something the next stage reads; this makes text for a person
to paste into an upload form, and most runs here never reach one — they are watched
on this machine, or cut again tomorrow. So the switch is off unless somebody says
otherwise, and "somebody" includes the run itself: an upload cannot happen without a
title, so a run that is really publishing is not asked twice.
"""

from __future__ import annotations

import json

from .. import parts
from ..context import AppContext
from ..job import Part, VideoJob
from .idea import LANG_NAMES

SYSTEM = (
    "You write metadata for viral vertical short videos. Respond with JSON only:\n"
    '{"title": "<max 90 chars, hooky, no clickbait-brackets spam>", '
    '"description": "<2-3 sentences>", "tags": ["<tag>", ...max 12], "hashtags": ["#tag", ...max 4]}'
)

# Told to the writer only when the story really is split. A one-part video must not be
# labelled "episode 1 of 1", and a serial's episode number belongs in the title where
# the language wants it, so the instruction asks for it rather than prefixing it later.
SERIAL_RULE = (
    "\nThis is EPISODE {n} OF {total} of one continuous story, published as its own video. "
    "Work the episode number into the title so a viewer can tell the order at a glance, "
    "and write the description for someone who may be starting here. "
    "Do not spoil what happens in later episodes."
)


def _write(job: VideoJob, part: Part, ctx: AppContext, total: int) -> None:
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    scenes = parts.scenes_by_part(job.scenes, part.number)
    script_text = " ".join(s.text for s in scenes if not s.is_ad)
    user = (
        f"Topic: {job.topic}\nScript: {script_text}\n"
        f"Write title/description/hashtags in {lang}; tags in English."
        + (SERIAL_RULE.format(n=part.number, total=total) if total > 1 else "")
    )
    meta = ctx.llm.complete_json("metadata", SYSTEM, user)

    hashtags = meta.get("hashtags", [])
    if "#Shorts" not in hashtags:
        hashtags.append("#Shorts")
    description = meta.get("description", "").strip()
    if ctx.ad and ctx.ad.description.snippet:
        description += "\n\n" + ctx.ad.description.snippet.format(url=ctx.ad.url)
    description += "\n\n" + " ".join(hashtags)

    part.metadata = {
        "title": meta["title"][:100],
        "description": description,
        "tags": meta.get("tags", [])[:15],
        "topic": job.topic,
        "lang": ctx.params.lang,
        "content_type": ctx.params.content_type,
        "part": part.number,
        "parts": total,
        "duration_s": round(sum(s.duration for s in scenes), 2),
        "file": str(part.file) if part.file else "",
    }
    name = f"metadata_part_{part.number:02d}.json" if total > 1 else "metadata.json"
    (job.workdir / name).write_text(
        json.dumps(part.metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def wanted(ctx: AppContext) -> bool:
    """Whether this run wants a title, a description and tags at all.

    Three things say yes, and only the first is a preference. `write_metadata` is the
    operator setting the switch. A breakpoint on this stage is the operator asking to
    READ the result, which is not a thing to ask about text nobody wrote. And an
    upload answers for both of them: a video cannot go to a platform without a title,
    so a run with an account behind it and the dry run off gets metadata whatever the
    switch says — the alternative is a chain that walks all the way to the uploader
    and dies there, an hour of generation after the mistake was made.

    A local "account" is not an upload: it reports where the file already is.
    """
    p = ctx.params
    if p.write_metadata or "metadata" in p.breakpoints:
        return True
    acc = ctx.account
    return not p.dry_run and acc is not None and acc.platform != "local"


def run(job: VideoJob, ctx: AppContext) -> None:
    if not wanted(ctx):
        return
    write_all(job, ctx)


def write_all(job: VideoJob, ctx: AppContext) -> None:
    """Describe every episode that is cut and has none yet, whatever the switch says.

    Split out of :func:`run` for the one caller that IS the answer to the question
    `run` asks: a press on the metadata button in the montage room. The operator
    pressing it has asked for metadata by pressing it, and a button that quietly did
    nothing because of a switch two screens away would be the worst of both."""
    parts.sync(job)
    total = len(job.parts)
    # only the episodes that have actually been cut, and only the ones not described
    # yet — re-entering the stage is how LATER episodes get their metadata
    todo = [p for p in parts.ready(job) if p.file and not p.metadata]
    for n, part in enumerate(todo, start=1):
        _write(job, part, ctx, total)
        ctx.progress("metadata", n, len(todo))
