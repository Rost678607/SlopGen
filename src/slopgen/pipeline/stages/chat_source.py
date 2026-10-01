"""Chat stage 0: get the conversation.

The stage is a dispatch and almost nothing else, which is the point of it. Where a
conversation came from — a subreddit, a Telegram session, an export file on disk, the
operator's own typing, a model asked to invent one — changes everything about how it
is FETCHED and nothing at all about what it is afterwards. So each source's business
ends here, at a list of :class:`~..job.ChatMsg`, and not one stage downstream asks
where they came from.

`manual` is the source that does nothing, and it is the default. A conversation typed
into the chat room, or imported there by hand, is already on the job by the time the
run starts — the room writes it straight onto a parked job — so the stage's whole job
is to notice that and let it through. A run started with `manual` and no messages is
not an error either: it parks on this stage's breakpoint with an empty conversation,
which is exactly what "open the room and build me one" looks like from the CLI.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..context import AppContext
from ..job import VideoJob

log = logging.getLogger(__name__)


def _manual(job: VideoJob, ctx: AppContext) -> None:
    """Whatever is already on the job, which is the room's doing. Nothing to fetch."""
    return


def _unbuilt(what: str) -> Callable[[VideoJob, AppContext], None]:
    """A source that is named but not yet written.

    It raises rather than quietly producing an empty conversation, because the two
    look identical three stages later and only one of them is somebody's mistake."""

    def run(job: VideoJob, ctx: AppContext) -> None:
        raise ValueError(
            f"the {what} source is not built yet — build the conversation in the chat "
            f"room instead (source 'manual'), or import an export file"
        )

    return run


SOURCES: dict[str, Callable[[VideoJob, AppContext], None]] = {
    "manual": _manual,
    "reddit": _unbuilt("reddit"),
    "telegram": _unbuilt("telegram"),
    "import": _unbuilt("export-file"),
}


def run(job: VideoJob, ctx: AppContext) -> None:
    cfg = ctx.chat
    fetch = SOURCES.get(cfg.source)
    if fetch is None:
        raise ValueError(f"unknown chat source {cfg.source!r} — one of {sorted(SOURCES)}")
    fetch(job, ctx)
    if cfg.title and not job.chat_title:
        job.chat_title = cfg.title
    ctx.progress("source", len(job.messages), len(job.messages))
