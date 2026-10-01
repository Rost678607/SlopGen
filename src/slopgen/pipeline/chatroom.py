"""The chat room: one conversation, built and argued with by hand.

Every other mode's operator writes a BRIEF and reads back what a model made of it.
This one's writes the thing itself — the conversation is not an input to the video,
it is the video — so the screen is not a list of what a stage produced but the
document the whole run is made from, and the first stage of the chain exists mainly
to park a run in front of it (`stages.chat_source`).

This module is that document and the operations over it, in the shape
:mod:`.montage` already established: nothing here talks to an LLM and nothing here
decides anything. What it owes the operator in return is that the conversation stays
internally honest — that a reply still points at the message it answers after the
message above it was dropped, that a person renamed in one place is renamed in all of
them, and that nothing silently refers to a message that is no longer there.

**A reply is an index, and indices move.** `ChatMsg.reply_to` points into the list
itself, because what a reply answers is a fact about the conversation and has to
survive the scenes being re-cut, re-voiced and re-laid. The price is that every
operation that moves a message has to carry the pointers with it, which is what
:func:`_remap` is for — one map from old position to new, applied to every reply in
one pass. A reply to a message that is GONE becomes no reply at all rather than a
reply to whatever has slid into that slot: the first is visibly missing and the
second is a quiet lie.

**Editing the conversation does not re-lay the scenes.** `stages.chat_script.lay`
rebuilds the timeline from the messages and casts the voices while it does it, so
calling it on every keystroke would throw away the audio of every line the operator
had not touched. The room edits messages; the scenes appear when the operator presses
`script`, exactly as the montage room runs `tts` by hand. Until then `ChatMsg.scene`
is stale on purpose and the room reads the conversation from the messages, never from
the scenes.
"""

from __future__ import annotations

import logging
import re

from .job import ChatMsg, VideoJob

log = logging.getLogger(__name__)

# How a pasted line names its author: `Ник: текст`. Deliberately narrow — a colon
# inside a sentence is far commoner than one after a nickname, so the name has to be
# short, free of sentence punctuation, and at the very start of the line.
_PASTED = re.compile(r"^\s*([^\s:][^:\n]{0,31}?)\s*:\s+(\S.*)$")


class ChatError(Exception):
    """Something the operator asked for that the conversation cannot be. Carries a
    sentence worth showing them — the web layer turns it into a 422 and the message
    goes on screen unchanged."""


def _at(job: VideoJob, i: int) -> ChatMsg:
    if not (0 <= i < len(job.messages)):
        raise ChatError(f"there is no message {i} in this conversation")
    return job.messages[i]


def _remap(job: VideoJob, moved: dict[int, int]) -> None:
    """Carry every reply pointer across a rearrangement.

    `moved` is old position → new position; a position absent from it has been
    removed, and a reply into it is dropped rather than re-aimed. Done in one pass
    over a snapshot of the old values, because remapping in place would read pointers
    this very loop had already rewritten."""
    old = [m.reply_to for m in job.messages]
    for msg, was in zip(job.messages, old):
        msg.reply_to = moved.get(was, -1) if was >= 0 else -1


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------


def read(job: VideoJob, ctx) -> dict:
    """The conversation as the screen reads it: the messages, and who is in it.

    The cast is derived from the messages rather than stored beside them, so a person
    who stops being in the conversation stops being in the cast, and somebody typed in
    for one line turns up in it without being carded first. `carded` is the difference
    between the two — a name with a card behind it has a picture and a voice that
    outlive this video, and one without is just a name on a bubble."""
    skin = ctx.chat_skin
    cfg = ctx.chat
    cast = []
    for name in dict.fromkeys(m.persona for m in job.messages):
        card = ctx.store.personas.get(name)
        cast.append({
            "name": name,
            "carded": card is not None,
            "handle": card.handle if card else "",
            "avatar": card.avatar if card else "",
            "voice": card.voice if card else "",
            "colour": card.colour if card else "",
            "lines": sum(1 for m in job.messages if m.persona == name),
        })
    return {
        "messages": [
            {
                "i": i,
                "persona": m.persona,
                "text": m.text,
                # what it said before the translation pass; shown beside the
                # translation, because reviewing one without the other is not
                # reviewing anything
                "source_text": m.source_text,
                "reply_to": m.reply_to,
                "reactions": [[e, n] for e, n in m.reactions],
                "score": m.score,
                "stamp": m.stamp,
                "excerpt": m.excerpt,
                "clear_before": m.clear_before,
                "nick": m.nick,
                "avatar": m.avatar,
                "pinned": m.pinned,
                # which line of the timeline this message became, once there is one.
                # -1 until `script` has laid them, which is the ordinary state of a
                # conversation still being built.
                "scene": m.scene,
            }
            for i, m in enumerate(job.messages)
        ],
        "cast": cast,
        "title": job.chat_title,
        "skin": skin.key,
        # reddit is an island (see `chat.skins.compatible`), so the room says which
        # half of the world this conversation lives in rather than letting the
        # operator discover it at the render
        "tree": skin.tree,
        "votes": skin.votes,
        "me": cfg.me,
        "excerpts": 1 + max((m.excerpt for m in job.messages), default=0),
        "blocking": blocking(job, ctx),
    }


def blocking(job: VideoJob, ctx) -> list[str]:
    """What stands between this conversation and a video, in the operator's words.

    Not validation — nothing here refuses anything — but the list the room shows so
    that "why is the render button grey" is answered on the screen rather than three
    stages later in a traceback."""
    out: list[str] = []
    if not job.messages:
        out.append("empty")
    if any(not m.text.strip() for m in job.messages):
        out.append("blank_line")
    if ctx.chat_skin.votes and any(m.reactions for m in job.messages):
        # the two never convert, which is the whole of why reddit is kept apart
        out.append("reactions_in_a_thread")
    return out


# --------------------------------------------------------------------------
# the operations
# --------------------------------------------------------------------------


def add(job: VideoJob, at: int, persona: str, text: str = "") -> int:
    """Put a new message at `at` (the end when it is past it). Returns where it went."""
    at = max(0, min(int(at), len(job.messages)))
    moved = {i: (i if i < at else i + 1) for i in range(len(job.messages))}
    excerpt = job.messages[at - 1].excerpt if at else (
        job.messages[0].excerpt if job.messages else 0)
    job.messages.insert(at, ChatMsg(persona=persona.strip(), text=text,
                                    excerpt=excerpt, pinned=True))
    _remap(job, moved)
    return at


def drop(job: VideoJob, i: int) -> None:
    """Take one message out, and un-aim every reply that pointed at it.

    A reply to a deleted message becomes no reply rather than a reply to whatever
    moved into that slot. The quiet version of this bug puts somebody's answer under
    a stranger's line and nothing on the screen says so."""
    _at(job, i)
    moved = {k: (k if k < i else k - 1) for k in range(len(job.messages)) if k != i}
    job.messages.pop(i)
    _remap(job, moved)


def move(job: VideoJob, i: int, to: int) -> int:
    """Drag one message to another place in the conversation."""
    _at(job, i)
    to = max(0, min(int(to), len(job.messages) - 1))
    if to == i:
        return i
    order = list(range(len(job.messages)))
    order.insert(to, order.pop(i))
    moved = {old: new for new, old in enumerate(order)}
    job.messages = [job.messages[k] for k in order]
    _remap(job, moved)
    return to


def set_text(job: VideoJob, i: int, text: str) -> None:
    """Rewrite what a message says.

    Pins it (`ChatMsg.pinned`), which is what stops a later pass of the translator
    from undoing the edit — the same promise a pinned shot and a pinned effect make
    in the montage room."""
    msg = _at(job, i)
    msg.text = str(text)
    msg.pinned = True


def edit(job: VideoJob, i: int, **fields) -> None:
    """Change the things about a message that are not its text.

    One entry point rather than eight, because the screen edits them in one panel and
    a request that set two of them through two routes could leave the conversation
    half-changed if the second failed."""
    msg = _at(job, i)
    if "persona" in fields:
        msg.persona = str(fields["persona"]).strip()
    if "reply_to" in fields:
        to = int(fields["reply_to"])
        if to == i:
            raise ChatError("a message cannot be a reply to itself")
        if to >= 0 and not (0 <= to < len(job.messages)):
            raise ChatError(f"there is no message {to} to answer")
        if to >= 0 and _answers(job, to, i):
            raise ChatError("that would make a reply answer itself round a circle")
        msg.reply_to = to
    if "stamp" in fields:
        msg.stamp = str(fields["stamp"])
    if "nick" in fields:
        msg.nick = str(fields["nick"])
    if "avatar" in fields:
        msg.avatar = str(fields["avatar"])
    if "score" in fields:
        msg.score = int(fields["score"])
    if "clear_before" in fields:
        msg.clear_before = bool(fields["clear_before"])
    if "pinned" in fields:
        msg.pinned = bool(fields["pinned"])


def _answers(job: VideoJob, start: int, target: int) -> bool:
    """Whether following the replies up from `start` reaches `target`.

    A cycle in the reply chain is not merely wrong, it hangs the tree walk the reddit
    skin does to find a comment's depth — so it is refused here, where there is a
    person to tell."""
    seen, at = set(), start
    while 0 <= at < len(job.messages) and at not in seen:
        if at == target:
            return True
        seen.add(at)
        at = job.messages[at].reply_to
    return False


def set_reactions(job: VideoJob, i: int, pairs) -> None:
    """Replace the emoji on one message, in the order they land."""
    msg = _at(job, i)
    out: list[tuple[str, int]] = []
    for item in pairs or []:
        emoji = str(item[0] if isinstance(item, (list, tuple)) else item).strip()
        count = int(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else 1
        if emoji:
            out.append((emoji, max(1, count)))
    msg.reactions = out


def split(job: VideoJob, i: int) -> None:
    """Begin a new excerpt at message `i`, and renumber everything under it.

    An excerpt is a separate piece of conversation in the same video, with a swipe
    between it and the last — so this is the one edit that changes a run of messages
    rather than one of them. Splitting at 0, or at a message that already begins an
    excerpt, joins it back to the one before instead: the operation is the boundary,
    and pressing it twice puts the boundary back where it was."""
    _at(job, i)
    if i == 0:
        raise ChatError("the first message already begins the first excerpt")
    begins = job.messages[i].excerpt != job.messages[i - 1].excerpt
    for msg in job.messages[i:]:
        msg.excerpt += -1 if begins else 1
    _renumber_excerpts(job)


def _renumber_excerpts(job: VideoJob) -> None:
    """Close the gaps, so an excerpt number always means "the Nth piece"."""
    seen: dict[int, int] = {}
    for msg in job.messages:
        if msg.excerpt not in seen:
            seen[msg.excerpt] = len(seen)
    for msg in job.messages:
        msg.excerpt = seen[msg.excerpt]


def rename(job: VideoJob, was: str, now: str) -> int:
    """Rename one participant everywhere they appear. Returns how many lines moved.

    On the conversation only: the CARD keeps its name, because a card is a thing that
    outlives this video and renaming it is the config panel's business. What this
    serves is the import that got somebody's name wrong in every line."""
    now = now.strip()
    if not now:
        raise ChatError("a participant needs a name")
    n = 0
    for msg in job.messages:
        if msg.persona == was:
            msg.persona = now
            n += 1
    return n


def import_lines(job: VideoJob, text: str, at: int = -1) -> int:
    """Parse a pasted block into messages. Returns how many were added.

    `Ник: текст` starts a new message and anything else continues the one before, so
    a message somebody wrote across three lines arrives as one message across three
    lines. A paste with no names in it at all becomes a run of messages from nobody,
    which is an honest reading of it and leaves the operator one `persona` field per
    message rather than a parse they have to undo.

    This is the paste box, not the export reader: an export carries authors, times,
    replies and reactions as structured fields, and guessing those out of its rendered
    text would throw away everything that makes it worth reading."""
    at = len(job.messages) if at < 0 else max(0, min(at, len(job.messages)))
    fresh: list[ChatMsg] = []
    for line in str(text).replace("\r", "").split("\n"):
        hit = _PASTED.match(line)
        if hit:
            fresh.append(ChatMsg(persona=hit.group(1).strip(),
                                 text=hit.group(2).strip(), pinned=True))
        elif fresh and line.strip():
            fresh[-1].text += "\n" + line.strip()
        elif line.strip():
            fresh.append(ChatMsg(persona="", text=line.strip(), pinned=True))
    if not fresh:
        return 0
    excerpt = job.messages[at - 1].excerpt if at else 0
    for msg in fresh:
        msg.excerpt = excerpt
    moved = {i: (i if i < at else i + len(fresh)) for i in range(len(job.messages))}
    job.messages[at:at] = fresh
    _remap(job, moved)
    return len(fresh)
