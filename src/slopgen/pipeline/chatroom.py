"""The chat room: the conversations a video is made of, built and argued with by hand.

Every other mode's operator writes a BRIEF and reads back what a model made of it.
This one's writes the thing itself — the conversations are not an input to the video,
they are the video — so the screen is not a list of what a stage produced but the
document the whole run is made from, and the first stage of the chain exists mainly
to park a run in front of it (`stages.chat_source`).

**The unit is a conversation, not a message.** A video is several pieces of
conversation shown one after another with a swipe between them, which was the format
from the first description of it, so a piece is a :class:`~.job.Conversation` with a
list of its own. Writing the pieces as a number on every message was the first attempt
and it was wrong in the way that matters: with them expressed as a field there was
nothing to count, nothing to reorder, and nothing for a source to fetch three OF — so
neither this room nor the fetchers had an object to hold. What is a thing in the format
has to be a thing in the model.

This module is that document and the operations over it, in the shape :mod:`.montage`
established: nothing here talks to an LLM and nothing here decides anything. What it
owes the operator in return is that the conversations stay internally honest — that a
reply still points at the message it answers after the one above it was dropped, that a
person renamed in one place is renamed in all of them, and that nothing silently refers
to a message that is no longer there.

**A reply is an index, and indices move.** `ChatMsg.reply_to` points into its own
conversation's list, because what a reply answers is a fact about the conversation it
was had in — nothing in one piece can answer anything in another. The price is that
every operation moving a message carries the pointers with it, which is what
:func:`_remap` is for. A reply to a message that is GONE becomes no reply at all rather
than a reply to whatever has slid into that slot: the first is visibly missing and the
second is a quiet lie.

**Editing does not re-lay the scenes.** `stages.chat_script.lay` rebuilds the timeline
from the conversations and casts the voices while it does it, so calling it on every
keystroke would throw away the audio of every line the operator had not touched. The
room edits conversations; the scenes appear when the operator presses `script`, exactly
as the montage room runs `tts` by hand.
"""

from __future__ import annotations

import logging
import re

from .job import ChatMsg, Conversation, VideoJob

log = logging.getLogger(__name__)

# How a pasted line names its author: `Ник: текст`. Deliberately narrow — a colon
# inside a sentence is far commoner than one after a nickname, so the name has to be
# short, free of sentence punctuation, and at the very start of the line.
_PASTED = re.compile(r"^\s*([^\s:][^:\n]{0,31}?)\s*:\s+(\S.*)$")


class ChatError(Exception):
    """Something the operator asked for that the conversations cannot be. Carries a
    sentence worth showing them — the web layer turns it into a 422 and the message
    goes on screen unchanged."""


def conv_at(job: VideoJob, c: int) -> Conversation:
    if not (0 <= c < len(job.conversations)):
        raise ChatError(f"there is no conversation {c} in this video")
    return job.conversations[c]


def _msg(conv: Conversation, i: int) -> ChatMsg:
    if not (0 <= i < len(conv.messages)):
        raise ChatError(f"there is no message {i} in this conversation")
    return conv.messages[i]


def _remap(conv: Conversation, moved: dict[int, int]) -> None:
    """Carry every reply pointer across a rearrangement of one conversation.

    `moved` is old position → new position; a position absent from it has been
    removed, and a reply into it is dropped rather than re-aimed. Done in one pass over
    a snapshot of the old values, because remapping in place would read pointers this
    very loop had already rewritten."""
    old = [m.reply_to for m in conv.messages]
    for msg, was in zip(conv.messages, old):
        msg.reply_to = moved.get(was, -1) if was >= 0 else -1


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------


def read(job: VideoJob, ctx) -> dict:
    """The video's conversations as the screen reads them, and who is in them.

    The cast is derived rather than stored beside them, so a person who stops being in
    the video stops being in the cast, and somebody typed in for one line turns up in
    it without being carded first. `carded` is the difference between the two — a name
    with a card behind it has a picture and a voice that outlive this video, and one
    without is just a name on a bubble."""
    skin = ctx.chat_skin
    cfg = ctx.chat
    everyone = job.messages
    cast = []
    for name in dict.fromkeys(m.persona for m in everyone):
        card = ctx.store.personas.get(name)
        # what the import found for them, when it found anything: the room offers it
        # as the picture to put on a card rather than making one behind their back
        found = next((m.avatar for m in everyone if m.persona == name and m.avatar), "")
        cast.append({
            "name": name,
            "carded": card is not None,
            "handle": card.handle if card else "",
            "avatar": (card.avatar if card else "") or found,
            "found": found,
            "voice": card.voice if card else "",
            "colour": card.colour if card else "",
            "lines": sum(1 for m in everyone if m.persona == name),
        })
    return {
        "conversations": [
            {
                "c": c,
                "title": conv.title,
                "source": conv.source,
                "lines": len(conv.messages),
                "messages": [_msg_json(i, m) for i, m in enumerate(conv.messages)],
            }
            for c, conv in enumerate(job.conversations)
        ],
        "cast": cast,
        "title": job.chat_title,
        "skin": skin.key,
        # reddit is an island (see `chat.skins.compatible`), so the room says which
        # half of the world these conversations live in rather than letting the
        # operator discover it at the render
        "tree": skin.tree,
        "votes": skin.votes,
        "me": cfg.me,
        "want": cfg.want,
        "blocking": blocking(job, ctx),
    }


def _msg_json(i: int, m: ChatMsg) -> dict:
    return {
        "i": i,
        "persona": m.persona,
        "text": m.text,
        # what it said before the translation pass; shown beside the translation,
        # because reviewing one without the other is not reviewing anything
        "source_text": m.source_text,
        "reply_to": m.reply_to,
        "reactions": [[e, n] for e, n in m.reactions],
        "score": m.score,
        "stamp": m.stamp,
        "clear_before": m.clear_before,
        "nick": m.nick,
        "avatar": m.avatar,
        "pinned": m.pinned,
        # which line of the timeline this message became, once there is one. -1 until
        # `script` has laid them, which is the ordinary state of a video being built.
        "scene": m.scene,
    }


def blocking(job: VideoJob, ctx) -> list[str]:
    """What stands between these conversations and a video, in the operator's words.

    Not validation — nothing here refuses anything — but the list the room shows so
    that "why is the render button grey" is answered on the screen rather than three
    stages later in a traceback."""
    out: list[str] = []
    if not job.messages:
        out.append("empty")
    if any(not m.text.strip() for m in job.messages):
        out.append("blank_line")
    if any(not c.messages for c in job.conversations):
        out.append("empty_conversation")
    if ctx.chat_skin.votes and any(m.reactions for m in job.messages):
        # the two never convert, which is the whole of why reddit is kept apart
        out.append("reactions_in_a_thread")
    return out


# --------------------------------------------------------------------------
# the conversations
# --------------------------------------------------------------------------


def add_conversation(job: VideoJob, at: int = -1, title: str = "") -> int:
    """Put another piece of conversation into the video. Returns where it went."""
    at = len(job.conversations) if at < 0 else max(0, min(at, len(job.conversations)))
    job.conversations.insert(at, Conversation(title=title.strip()))
    return at


def drop_conversation(job: VideoJob, c: int) -> None:
    """Take one piece out, with everything said in it.

    Nothing has to be remapped: a reply never crosses a seam, so the pointers inside
    the conversations left standing were never aimed at this one."""
    conv_at(job, c)
    job.conversations.pop(c)


def move_conversation(job: VideoJob, c: int, to: int) -> int:
    """Show this piece earlier or later in the video."""
    conv_at(job, c)
    to = max(0, min(int(to), len(job.conversations) - 1))
    if to != c:
        job.conversations.insert(to, job.conversations.pop(c))
    return to


def set_conversation(job: VideoJob, c: int, **fields) -> None:
    """Name a piece, or record where it came from.

    The title is what the header bar says while this one is up, which is a property of
    the conversation and not of the run: three threads in one video are three different
    chats, and one name over all of them is the tell that the screenshot is fake."""
    conv = conv_at(job, c)
    if "title" in fields:
        conv.title = str(fields["title"]).strip()
    if "source" in fields:
        conv.source = str(fields["source"]).strip()


def split(job: VideoJob, c: int, i: int) -> int:
    """Cut one conversation in two at message `i`; the tail becomes the next piece.

    A real operation now rather than a boundary flag: what comes out is a second
    conversation, with its own name and its own place in the order, which is what the
    swipe is drawn between. Replies that pointed across the cut are dropped, because
    a reply cannot cross a seam — nothing in one piece answers anything in another."""
    conv = conv_at(job, c)
    _msg(conv, i)
    if i == 0:
        raise ChatError("there would be nothing left in the first half")
    tail = conv.messages[i:]
    conv.messages = conv.messages[:i]
    fresh = Conversation(title=conv.title, source=conv.source, messages=tail)
    moved = {old: old - i for old in range(i, i + len(tail))}
    _remap(fresh, moved)
    _remap(conv, {k: k for k in range(len(conv.messages))})
    job.conversations.insert(c + 1, fresh)
    return c + 1


def join(job: VideoJob, c: int) -> None:
    """Fold this piece back into the one before it, which is the undo of a split."""
    conv = conv_at(job, c)
    if c == 0:
        raise ChatError("there is nothing before the first conversation to join it to")
    before = job.conversations[c - 1]
    shift = len(before.messages)
    for msg in conv.messages:
        if msg.reply_to >= 0:
            msg.reply_to += shift
    before.messages.extend(conv.messages)
    job.conversations.pop(c)


# --------------------------------------------------------------------------
# the messages
# --------------------------------------------------------------------------


def add(job: VideoJob, c: int, at: int, persona: str, text: str = "") -> int:
    """Put a new message at `at` (the end when it is past it). Returns where it went."""
    conv = conv_at(job, c)
    at = max(0, min(int(at), len(conv.messages)))
    moved = {i: (i if i < at else i + 1) for i in range(len(conv.messages))}
    conv.messages.insert(at, ChatMsg(persona=persona.strip(), text=text, pinned=True))
    _remap(conv, moved)
    return at


def drop(job: VideoJob, c: int, i: int) -> None:
    """Take one message out, and un-aim every reply that pointed at it.

    A reply to a deleted message becomes no reply rather than a reply to whatever moved
    into that slot. The quiet version of this bug puts somebody's answer under a
    stranger's line and nothing on the screen says so."""
    conv = conv_at(job, c)
    _msg(conv, i)
    moved = {k: (k if k < i else k - 1) for k in range(len(conv.messages)) if k != i}
    conv.messages.pop(i)
    _remap(conv, moved)


def move(job: VideoJob, c: int, i: int, to: int) -> int:
    """Drag one message to another place in its own conversation."""
    conv = conv_at(job, c)
    _msg(conv, i)
    to = max(0, min(int(to), len(conv.messages) - 1))
    if to == i:
        return i
    order = list(range(len(conv.messages)))
    order.insert(to, order.pop(i))
    moved = {old: new for new, old in enumerate(order)}
    conv.messages = [conv.messages[k] for k in order]
    _remap(conv, moved)
    return to


def set_text(job: VideoJob, c: int, i: int, text: str) -> None:
    """Rewrite what a message says.

    Pins it (`ChatMsg.pinned`), which is what stops a later pass of the translator from
    undoing the edit — the same promise a pinned shot and a pinned effect make in the
    montage room."""
    msg = _msg(conv_at(job, c), i)
    msg.text = str(text)
    msg.pinned = True


def edit(job: VideoJob, c: int, i: int, **fields) -> None:
    """Change the things about a message that are not its text.

    One entry point rather than eight, because the screen edits them in one panel and a
    request that set two of them through two routes could leave the conversation
    half-changed if the second failed."""
    conv = conv_at(job, c)
    msg = _msg(conv, i)
    if "persona" in fields:
        msg.persona = str(fields["persona"]).strip()
    if "reply_to" in fields:
        to = int(fields["reply_to"])
        if to == i:
            raise ChatError("a message cannot be a reply to itself")
        if to >= 0 and not (0 <= to < len(conv.messages)):
            raise ChatError(f"there is no message {to} in this conversation to answer")
        if to >= 0 and _answers(conv, to, i):
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


def _answers(conv: Conversation, start: int, target: int) -> bool:
    """Whether following the replies up from `start` reaches `target`.

    A cycle in the reply chain is not merely wrong, it hangs the tree walk the reddit
    skin does to find a comment's depth — so it is refused here, where there is a
    person to tell."""
    seen, at = set(), start
    while 0 <= at < len(conv.messages) and at not in seen:
        if at == target:
            return True
        seen.add(at)
        at = conv.messages[at].reply_to
    return False


def set_reactions(job: VideoJob, c: int, i: int, pairs) -> None:
    """Replace the emoji on one message, in the order they land."""
    msg = _msg(conv_at(job, c), i)
    out: list[tuple[str, int]] = []
    for item in pairs or []:
        emoji = str(item[0] if isinstance(item, (list, tuple)) else item).strip()
        count = int(item[1]) if isinstance(item, (list, tuple)) and len(item) > 1 else 1
        if emoji:
            out.append((emoji, max(1, count)))
    msg.reactions = out


def rename(job: VideoJob, was: str, now: str) -> int:
    """Rename one participant everywhere they appear. Returns how many lines moved.

    On the conversations only: the CARD keeps its name, because a card is a thing that
    outlives this video and renaming it is the config panel's business. What this
    serves is the import that got somebody's name wrong in every line."""
    now = now.strip()
    if not now:
        raise ChatError("a participant needs a name")
    n = 0
    for conv in job.conversations:
        for msg in conv.messages:
            if msg.persona == was:
                msg.persona = now
                n += 1
    return n


def import_lines(job: VideoJob, c: int, text: str, at: int = -1) -> int:
    """Parse a pasted block into messages. Returns how many were added.

    `Ник: текст` starts a new message and anything else continues the one before, so a
    message somebody wrote across three lines arrives as one message across three
    lines. A paste with no names in it at all becomes a run of messages from nobody,
    which is an honest reading of it and leaves the operator one `persona` field per
    message rather than a parse they have to undo.

    This is the paste box, not the export reader: an export carries authors, times,
    replies and reactions as structured fields, and guessing those out of its rendered
    text would throw away everything that makes it worth reading."""
    conv = conv_at(job, c)
    at = len(conv.messages) if at < 0 else max(0, min(at, len(conv.messages)))
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
    moved = {i: (i if i < at else i + len(fresh)) for i in range(len(conv.messages))}
    conv.messages[at:at] = fresh
    _remap(conv, moved)
    return len(fresh)
