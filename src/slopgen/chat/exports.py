"""Reading a conversation out of somebody's export.

Four formats, one shape out. Each of these is a file a person already has — they
pressed "export" in a client, or saved a page's JSON — and what matters about them all
is the same: they carry the authors, the times, what answers what and what was reacted
to as STRUCTURED fields. That is the whole reason this is a reader and not a paste box.
Guessing those back out of rendered text would throw away everything that makes an
export worth having, which is why `chatroom.import_lines` stays what it is and this is
separate.

Nothing here imports the pipeline, for the reason the rest of this package does not:
what comes back is this module's own :class:`Piece` and :class:`Line`, and the stage
that asked turns them into the job's conversations. It also means a parser can be run
on a file with nothing else loaded, which is how you find out why one export of the
hundred came back empty.

**What is deliberately dropped.** Pictures, stickers, files, voice messages, polls,
pins and joins. A chat video reads text aloud and draws it; a message that is a sticker
has nothing to read and nothing to draw, and a placeholder for it would be a line of
text nobody wrote. What they leave behind is a gap in the numbering, which costs
nothing — the replies are remapped onto what survived (:func:`_settle`), and a reply
whose target was dropped becomes no reply rather than a reply to the wrong line.

**Reddit is read as a tree and kept as one.** The post is the first message and every
comment answers its parent, so `reply_to` carries the whole thread shape and the reddit
skin draws it as the indent it is. Flattening it here would be the one thing this mode
refuses to do anywhere else.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# What an export may be, by what it turns out to hold. Sniffed rather than taken from
# the file name, because "result.json" is what three different programs call their
# export and an operator who renamed one is not wrong to expect it to still work.
FORMATS = ("telegram_json", "telegram_html", "discord_json", "reddit_json")


class ExportError(Exception):
    """A file that cannot be read as a conversation, said in a sentence worth showing
    the operator. Nothing here raises for a conversation that is merely EMPTY — that is
    an answer, and a different one from "this is not an export"."""


@dataclass
class Line:
    """One message, as an export gives it."""

    who: str
    text: str
    stamp: str = ""
    # Into THIS piece's own list, already remapped off whatever ids the export used
    # (see `_settle`). -1 is no reply, and is also what a reply to a dropped message
    # becomes.
    reply_to: int = -1
    reactions: list[tuple[str, int]] = field(default_factory=list)
    score: int = 0  # reddit karma; meaningless in a messenger and never drawn there
    # The file this person's picture was saved as, when the source had one. An export
    # never does — that is the first thing a client leaves out — so this is empty for
    # everything but a live read, and empty means the initials disc.
    avatar: str = ""
    # The DAY this was said, as `YYYY-MM-DD`, kept apart from the clock because it is
    # drawn apart from it: a messenger prints the time on the bubble and the date as a
    # separator between the days, and the second only appears where the first changes.
    # Empty where the source gave no date at all.
    day: str = ""


@dataclass
class Piece:
    """One conversation out of one export: what it is called, and what was said."""

    title: str = ""
    source: str = ""
    lines: list[Line] = field(default_factory=list)


def read(path: Path) -> list[Piece]:
    """Everything in one export file, as pieces of conversation.

    One piece for a messenger export (a chat is a chat) and one for a reddit thread.
    A list all the same, because a format that one day holds several — a folder of
    channels, a multireddit — should not change the shape of this."""
    raw = path.read_bytes()
    kind = sniff(raw)
    if kind is None:
        raise ExportError(
            f"{path.name} is not an export this reads: expected Telegram's JSON or "
            "HTML, a DiscordChatExporter JSON, or a reddit thread's .json"
        )
    text = raw.decode("utf-8", errors="replace")
    if kind == "telegram_html":
        pieces = telegram_html(text)
    else:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ExportError(f"{path.name} is not readable JSON ({e})")
        pieces = {"telegram_json": telegram_json,
                  "discord_json": discord_json,
                  "reddit_json": reddit_json}[kind](data)
    for p in pieces:
        p.source = p.source or path.name
    log.info("export %s: %s, %d piece(s), %d line(s)", path.name, kind, len(pieces),
             sum(len(p.lines) for p in pieces))
    return pieces


def sniff(raw: bytes) -> str | None:
    """Which of the four this is, by what it holds rather than what it is called."""
    head = raw[:4096].lstrip()
    if head[:1] in (b"<", b"\xef"):  # a BOM, then markup
        low = raw[:65536].lower()
        if b"class=\"message" in low or b"tgme_widget" in low or b"history" in low:
            return "telegram_html"
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if isinstance(data, list):
        # reddit hands back a pair of Listings: the post, then its comments
        if any(isinstance(x, dict) and x.get("kind") == "Listing" for x in data):
            return "reddit_json"
        return None
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("messages"), list):
        if "channel" in data or "guild" in data:
            return "discord_json"
        return "telegram_json"
    if data.get("kind") == "Listing":
        return "reddit_json"
    # a whole-account Telegram export: the chats are one level down
    if isinstance(data.get("chats"), dict):
        return "telegram_json"
    return None


# --------------------------------------------------------------------------
# telegram
# --------------------------------------------------------------------------


def telegram_json(data: dict) -> list[Piece]:
    """Telegram Desktop's `result.json`, for one chat or for a whole account.

    A whole-account export nests every chat under `chats.list`, and each of those is
    the same object a single-chat export IS — so the two are one reader with a loop
    around it rather than two that drift."""
    chats = data.get("chats")
    if isinstance(chats, dict) and isinstance(chats.get("list"), list):
        out = [_tg_chat(c) for c in chats["list"] if isinstance(c, dict)]
        return [p for p in out if p.lines]
    return [p for p in [_tg_chat(data)] if p.lines]


def _tg_chat(chat: dict) -> Piece:
    piece = Piece(title=str(chat.get("name") or "").strip())
    ids: dict[object, int] = {}
    pending: list[tuple[int, object]] = []
    for msg in chat.get("messages") or []:
        if not isinstance(msg, dict) or msg.get("type") != "message":
            continue  # service messages: joins, pins, calls — nobody said them
        text = _tg_text(msg.get("text"))
        if not text.strip():
            continue  # a sticker, a photo, a file: nothing to read and nothing to draw
        who = str(msg.get("from") or "").strip()
        line = Line(who=who, text=text, stamp=_clock(msg.get("date")),
                    day=_day(msg.get("date")),
                    reactions=_tg_reactions(msg.get("reactions")))
        ids[msg.get("id")] = len(piece.lines)
        if msg.get("reply_to_message_id") is not None:
            pending.append((len(piece.lines), msg["reply_to_message_id"]))
        piece.lines.append(line)
    _settle(piece, ids, pending)
    return piece


def _tg_text(text) -> str:
    """Telegram's text, which is a string, or a list of strings and entities.

    Only the WORDS are kept: an entity is bold, a link, a mention, a code span, and
    none of those survive being drawn as a chat bubble anyway. A link keeps its text
    rather than its href, because that is what the person saw."""
    if isinstance(text, str):
        return text
    if not isinstance(text, list):
        return ""
    out = []
    for bit in text:
        if isinstance(bit, str):
            out.append(bit)
        elif isinstance(bit, dict):
            out.append(str(bit.get("text") or ""))
    return "".join(out)


def _tg_reactions(raw) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        emoji = str(r.get("emoji") or "").strip()
        if emoji:
            out.append((emoji, max(1, int(r.get("count") or 1))))
    return out


# The HTML export, which is what somebody who exported "for reading" ends up with.
# Parsed with expressions and not a parser, because the one thing it has going for it
# is that it is machine-written: every message is the same handful of divs in the same
# order, and a dependency on an HTML library to read four of them would be a
# dependency the whole program carries for one file format.
_HTML_MSG = re.compile(
    r'<div class="message default clearfix(?P<joined> joined)?"[^>]*id="message(?P<id>\d+)"'
    r'(?P<body>.*?)(?=<div class="message |\Z)', re.S)
_HTML_FROM = re.compile(r'<div class="from_name">(?P<who>.*?)</div>', re.S)
_HTML_TEXT = re.compile(r'<div class="text">(?P<text>.*?)</div>', re.S)
_HTML_DATE = re.compile(r'<div class="pull_right date details"[^>]*title="(?P<at>[^"]*)"')
_HTML_REPLY = re.compile(r'<div class="reply_to details">.*?go_to_message(?P<id>\d+)', re.S)
_HTML_TITLE = re.compile(r'<div class="text bold">(?P<title>.*?)</div>', re.S)


def telegram_html(text: str) -> list[Piece]:
    """Telegram Desktop's `messages.html`, including the `messages2.html` beside it.

    A run of messages from one person carries the author on the FIRST only and
    `joined` on the rest, which is the export's own way of saying what the bubbles
    already say. So the author is carried forward, or a conversation comes back with
    nine tenths of its lines from nobody."""
    title = _HTML_TITLE.search(text)
    piece = Piece(title=_plain(title.group("title")) if title else "")
    ids: dict[object, int] = {}
    pending: list[tuple[int, object]] = []
    last_who = ""
    for m in _HTML_MSG.finditer(text):
        body = m.group("body")
        who_m = _HTML_FROM.search(body)
        who = _plain(who_m.group("who")) if who_m else ""
        if m.group("joined") and not who:
            who = last_who
        if who:
            last_who = who
        text_m = _HTML_TEXT.search(body)
        said = _plain(text_m.group("text")) if text_m else ""
        if not said.strip():
            continue
        at = _HTML_DATE.search(body)
        reply = _HTML_REPLY.search(body)
        ids[m.group("id")] = len(piece.lines)
        if reply:
            pending.append((len(piece.lines), reply.group("id")))
        when = at.group("at") if at else ""
        piece.lines.append(Line(who=who, text=said, stamp=_clock(when), day=_day(when)))
    _settle(piece, ids, pending)
    return [piece] if piece.lines else []


def _plain(markup: str) -> str:
    """One div's contents as the words that were in it: `<br>` is a line break, every
    other tag is dropped, and the entities are turned back into characters."""
    out = re.sub(r"<br\s*/?>", "\n", markup)
    out = re.sub(r"<[^>]+>", "", out)
    return html_mod.unescape(out).strip()


# --------------------------------------------------------------------------
# discord
# --------------------------------------------------------------------------


def discord_json(data: dict) -> list[Piece]:
    """DiscordChatExporter's JSON, which is one channel per file.

    The author's `nickname` wins over their `name`: the nickname is what everyone in
    that server actually sees above the message, and the name is the account's."""
    channel = data.get("channel") or {}
    guild = data.get("guild") or {}
    bits = [str(guild.get("name") or "").strip(), str(channel.get("name") or "").strip()]
    piece = Piece(title=" · ".join(b for b in bits if b))
    ids: dict[object, int] = {}
    pending: list[tuple[int, object]] = []
    for msg in data.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("type") or "Default") not in ("Default", "Reply"):
            continue  # joins, pins, calls — the channel's own noise
        said = str(msg.get("content") or "")
        if not said.strip():
            continue  # an attachment or an embed and nothing else
        author = msg.get("author") or {}
        who = str(author.get("nickname") or author.get("name") or "").strip()
        ids[msg.get("id")] = len(piece.lines)
        ref = msg.get("reference") or {}
        if ref.get("messageId"):
            pending.append((len(piece.lines), ref["messageId"]))
        piece.lines.append(Line(
            who=who, text=said, stamp=_clock(msg.get("timestamp")),
            day=_day(msg.get("timestamp")),
            reactions=_discord_reactions(msg.get("reactions")),
        ))
    _settle(piece, ids, pending)
    return [piece] if piece.lines else []


def _discord_reactions(raw) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        emoji = (r.get("emoji") or {})
        name = str(emoji.get("name") or "").strip()
        # a custom server emoji is an image with a `:name:`; there is no glyph to draw
        # and its picture is not in the export, so it is dropped rather than printed
        if name and not emoji.get("imageUrl", "").endswith((".png", ".gif", ".webp")):
            out.append((name, max(1, int(r.get("count") or 1))))
    return out


# --------------------------------------------------------------------------
# reddit
# --------------------------------------------------------------------------


def reddit_json(data) -> list[Piece]:
    """A thread's own `.json` — the post, then its comments, as the site serves them.

    Kept as a TREE: the post is the first line and every comment answers its parent, so
    `reply_to` carries the thread's whole shape and the reddit skin draws it as the
    indent it already is. The `more` stubs the site uses for "load 400 more replies" are
    not comments and are skipped — what they stand for is not in the file."""
    listings = data if isinstance(data, list) else [data]
    piece = Piece()
    post = _reddit_post(listings)
    if post is not None:
        piece.title = post["title"]
        piece.lines.append(Line(who=post["who"], text=post["text"],
                                score=post["score"], stamp=_clock(post["at"]),
                                day=_day(post["at"])))
    for listing in listings:
        for child in _children(listing):
            if child.get("kind") == "t1":
                _reddit_comment(piece, child.get("data") or {}, parent=0 if post else -1)
    return [piece] if piece.lines else []


def _reddit_post(listings: list) -> dict | None:
    for listing in listings:
        for child in _children(listing):
            if child.get("kind") != "t3":
                continue
            d = child.get("data") or {}
            title = str(d.get("title") or "").strip()
            body = str(d.get("selftext") or "").strip()
            return {
                "title": title,
                "who": f"u/{d.get('author') or 'somebody'}",
                # the title is the first thing anybody reads, so it is the first thing
                # said — with the body under it when there is one
                "text": "\n\n".join(x for x in (title, body) if x),
                "score": int(d.get("score") or 0),
                "at": d.get("created_utc"),
            }
    return None


def _children(listing) -> list[dict]:
    if not isinstance(listing, dict):
        return []
    data = listing.get("data") or {}
    kids = data.get("children")
    return [k for k in kids if isinstance(k, dict)] if isinstance(kids, list) else []


def _reddit_comment(piece: Piece, d: dict, parent: int) -> None:
    body = str(d.get("body") or "").strip()
    if not body or body in ("[deleted]", "[removed]"):
        return
    here = len(piece.lines)
    piece.lines.append(Line(
        who=f"u/{d.get('author') or 'somebody'}",
        text=body, score=int(d.get("score") or 0),
        reply_to=parent, stamp=_clock(d.get("created_utc")),
        day=_day(d.get("created_utc")),
    ))
    for child in _children(d.get("replies")):
        if child.get("kind") == "t1":
            _reddit_comment(piece, child.get("data") or {}, parent=here)


# --------------------------------------------------------------------------
# the bits every format needs
# --------------------------------------------------------------------------


def _settle(piece: Piece, ids: dict, pending: list[tuple[int, object]]) -> None:
    """Turn the export's own message ids into places in this piece's list.

    A reply whose target did not survive — a sticker, a photo, a deleted line — becomes
    no reply at all. That is the whole reason this is a second pass: an export numbers
    its messages and we number ours, and the two stop agreeing at the first thing we
    drop."""
    for at, target in pending:
        where = ids.get(target)
        if where is None:
            where = ids.get(str(target))
        if where is not None and where != at:
            piece.lines[at].reply_to = where


def _day(at) -> str:
    """The date as `YYYY-MM-DD`, out of whatever the export wrote.

    ISO rather than words, because which words depends on the language the VIDEO is in
    and this module does not know it — the render stage does, and turns one into the
    other (`stages.chat_render.day_label`)."""
    if at is None or at == "":
        return ""
    if isinstance(at, (int, float)):
        try:
            return datetime.fromtimestamp(float(at), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(at).strip()
    hit = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", text)
    if hit:
        return hit.group(0)
    # Telegram's HTML export writes `DD.MM.YYYY HH:MM:SS`
    hit = re.search(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)", text)
    if hit:
        return f"{hit.group(3)}-{hit.group(2)}-{hit.group(1)}"
    return ""


def _clock(at) -> str:
    """The time on the bubble: `HH:MM`, out of whatever the export wrote.

    An ISO stamp, a unix number, Telegram's `DD.MM.YYYY HH:MM:SS` — three spellings
    and one answer. Anything else comes back empty, which draws no time rather than a
    wrong one."""
    if at is None or at == "":
        return ""
    if isinstance(at, (int, float)):
        try:
            return datetime.fromtimestamp(float(at), tz=timezone.utc).strftime("%H:%M")
        except (OverflowError, OSError, ValueError):
            return ""
    text = str(at).strip()
    hit = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text)
    if hit:
        return f"{int(hit.group(1)):02d}:{hit.group(2)}"
    return ""
