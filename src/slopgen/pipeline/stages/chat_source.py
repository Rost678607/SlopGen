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

import asyncio
import logging
from typing import Callable

from pathlib import Path

from ...chat import exports, reddit, telegram
from ...llm import chat as chat_llm
from ..context import AppContext
from ..job import ChatMsg, Conversation, VideoJob

log = logging.getLogger(__name__)


def _manual(job: VideoJob, ctx: AppContext) -> None:
    """Whatever is already on the job, which is the room's doing. Nothing to fetch."""
    return


EXPORTS_DIR = "exports"


def exports_root(ctx: AppContext) -> Path:
    """Where the exports somebody has handed over live.

    A plain folder under `assets/`, like the avatars and the send sounds: an export is
    a file and has no properties to write down. What makes it a BASE rather than a
    one-off import is that it stays — the same thread is cut three different ways over
    a month, and re-uploading it each time is the operator's evening."""
    return ctx.g.paths.assets / EXPORTS_DIR


def pick(ctx: AppContext, name: str) -> Path:
    """One export by the name a run stored, inside the base and nowhere else.

    The name comes off a form, so it is resolved against the folder and checked to
    still be under it: a run is not a place to read `../../.env` from."""
    root = exports_root(ctx).resolve()
    at = (root / name).resolve()
    if not str(at).startswith(str(root)) or not at.is_file():
        raise ValueError(f"there is no export called {name!r} in assets/{EXPORTS_DIR}/")
    return at


def take(job: VideoJob, pieces) -> int:
    """Put parsed pieces onto the job as conversations. Returns how many landed.

    The one place an export becomes the pipeline's own objects, which is why it is
    here and not in `chat.exports`: that module is a reader and knows nothing about
    jobs, and this is the seam."""
    n = 0
    for piece in pieces:
        if not piece.lines:
            continue
        job.conversations.append(Conversation(
            title=piece.title, source=piece.source,
            messages=[ChatMsg(persona=ln.who, text=ln.text, stamp=ln.stamp,
                              reply_to=ln.reply_to, reactions=list(ln.reactions),
                              score=ln.score)
                      for ln in piece.lines],
        ))
        n += 1
    return n


def _import(job: VideoJob, ctx: AppContext) -> None:
    """Everything in one export file, as conversations.

    `chat_from` names the file inside the base. Which pieces of a multi-chat export
    are wanted is a judgement with the conversations in front of you, so a run that
    takes them all is the blunt answer and the room is the sharp one."""
    name = (ctx.params.chat_from or "").strip()
    if not name:
        raise ValueError(
            f"this run imports an export but was not told which — put the file in "
            f"assets/{EXPORTS_DIR}/ and name it"
        )
    try:
        pieces = exports.read(pick(ctx, name))
    except exports.ExportError as e:
        raise ValueError(str(e))
    if not take(job, pieces):
        raise ValueError(f"{name} holds no messages this can read")


def _reddit(job: VideoJob, ctx: AppContext) -> None:
    """A thread, or the first few of a subreddit.

    Which it is, is read off what the operator typed rather than asked as a second
    question: a URL with `comments/` in it names one thread and anything else names a
    place to take threads from. `want` is how many, because a video is several pieces
    of conversation and a source that could only ever bring back one would make a mode
    that cannot do what it was asked for."""
    where = (ctx.params.chat_from or "").strip()
    if not where:
        raise ValueError("this run reads reddit but was not told what — a thread's "
                         "address, or a subreddit")
    want = max(1, ctx.chat.want)
    try:
        if "comments/" in where:
            take(job, reddit.thread(where))
            return
        rows = reddit.listing(where, limit=max(25, want * 8))
        # Self-posts only: a link post is a picture with arguing under it, and the
        # picture is the half this mode cannot show.
        stories = [r for r in rows if r["text"].strip()] or rows
        # Which of them is worth a video is a judgement about whether anything HAPPENS
        # in them, and a score is evidence of attention rather than of that — so a
        # model reads the titles and the first lines and says (see `llm/chat.pick`).
        # It fails soft on purpose: without it the listing's own order is what a person
        # skimming would start from anyway.
        chosen = chat_llm.pick(ctx, stories, want)
        for row in ([stories[i] for i in chosen] or stories)[:want]:
            take(job, reddit.thread(row["url"]))
    except reddit.RedditError as e:
        raise ValueError(str(e))
    if not job.conversations:
        raise ValueError(f"nothing readable came back from {where}")


def _telegram(job: VideoJob, ctx: AppContext) -> None:
    """One chat's recent messages, read as the account this machine is signed in as."""
    where = (ctx.params.chat_from or "").strip()
    if not where:
        raise ValueError("this run reads Telegram but was not told which chat")
    try:
        pieces = asyncio.run(telegram.history(ctx.g.paths.state, where))
    except telegram.TelegramError as e:
        raise ValueError(str(e))
    if not take(job, pieces):
        raise ValueError(f"{where} has nothing readable in it")


def _invent(job: VideoJob, ctx: AppContext) -> None:
    """Conversations written from nothing, on whatever topic was given.

    `scenario` is the topic here, which is what it is in every other mode: the thing
    the operator wants this video to be about. Empty is a legitimate answer and means
    the writer picks the situation, which is the setting a loop runs on."""
    pieces = chat_llm.invent(
        ctx, topic=(ctx.params.scenario or "").strip(), want=max(1, ctx.chat.want),
        cast=list(ctx.chat.cast),
        length=(max(2, ctx.chat.invent_lo), max(3, ctx.chat.invent_hi)),
    )
    if not take(job, pieces):
        raise ValueError("the writer came back with nothing to say")


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
    "invent": _invent,
    "import": _import,
    "reddit": _reddit,
    "telegram": _telegram,
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
