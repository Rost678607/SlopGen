"""Reading Telegram as an ACCOUNT, which is the only way to read it at all.

The bot API cannot do this and no amount of care makes it: a bot sees only the chats
it was added to, and `getUpdates` hands it new messages with no history behind them.
What this mode needs is the opposite — somebody's own chats, as far back as they go —
and the only interface that serves that is MTProto, signed in as a person. So this is
a session: a phone number, a code Telegram sends to it, sometimes a two-step password,
and a `.session` file afterwards that is as good as being logged in.

That makes two things true that are worth saying out loud. The session file in
`state/` is a credential — anybody holding it is holding the account — which is why it
lives beside the other state and never near the repository. And the sign-in is
INTERACTIVE by nature: Telegram sends a code and waits for it, so there is no form
that can be filled in once. The flow here is therefore three steps a caller drives at
whatever pace it likes (:class:`Login`), which is what lets the terminal walk it with
`input()` and the browser walk the same one with three requests.

`TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are the application's own credentials, got
once from https://my.telegram.org/apps and kept in `.env` beside the bot token. They
identify the PROGRAM, not the account — the account is the session — and Telegram
refuses to talk to a client without them.

Nothing here imports the pipeline and nothing here imports telethon at module level:
what comes back is `exports.Piece`, the same shape a saved export parses into, and a
machine with no telethon installed can still draw a chat it was handed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import media as media_mod
from .exports import Line, Media, Piece, _clock, _day, _settle, custom_reaction

log = logging.getLogger(__name__)

ID_VAR, HASH_VAR = "TELEGRAM_API_ID", "TELEGRAM_API_HASH"
# Where the session lives: beside the other state, never near the repository. It is a
# credential in the strongest sense — holding the file IS being logged in — so it is
# named plainly rather than hidden, because a thing you cannot see is a thing you
# forget to delete.
SESSION = "telegram.session"


class TelegramError(Exception):
    """Something Telegram would not do, said in a sentence worth showing the operator."""


def credentials() -> tuple[int, str]:
    """The application's id and hash, or `(0, "")` when they have not been given."""
    raw = os.environ.get(ID_VAR, "").strip()
    try:
        api_id = int(raw) if raw else 0
    except ValueError:
        api_id = 0
    return api_id, os.environ.get(HASH_VAR, "").strip()


def session_path(state_dir: Path) -> Path:
    return Path(state_dir) / SESSION


def _telethon():
    try:
        from telethon import TelegramClient
        from telethon import errors as tg_errors
    except ImportError:
        raise TelegramError(
            "telethon is not installed — `pip install -r requirements.txt` inside the "
            "nix-shell, then try again")
    return TelegramClient, tg_errors


async def _connected(state_dir: Path, *, authorised: bool = True):
    """A connected client, and whether anybody is signed in on it.

    Raises rather than returning a client that cannot be used, because every caller
    that wants one wants it for reading somebody's chats, and "not signed in" is a
    thing to say once here instead of in five places."""
    api_id, api_hash = credentials()
    if not (api_id and api_hash):
        raise TelegramError(
            f"Telegram needs this program's own credentials: get them once at "
            f"https://my.telegram.org/apps and put them in .env as {ID_VAR} and {HASH_VAR}")
    TelegramClient, _ = _telethon()
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(session_path(state_dir)), api_id, api_hash)
    await client.connect()
    if authorised and not await client.is_user_authorized():
        await client.disconnect()
        raise TelegramError("nobody is signed in — log in to the Telegram account first")
    return client


async def status(state_dir: Path) -> dict:
    """Who, if anybody, this machine is signed in as."""
    api_id, api_hash = credentials()
    out = {"keys": bool(api_id and api_hash), "signed_in": False, "who": "",
           "session": session_path(state_dir).is_file()}
    if not out["keys"] or not out["session"]:
        return out
    try:
        client = await _connected(state_dir, authorised=False)
    except TelegramError:
        return out
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            out["signed_in"] = True
            out["who"] = _person(me)
    finally:
        await client.disconnect()
    return out


async def sign_out(state_dir: Path) -> None:
    """Log the account out and take the session file with it.

    Both halves, because either alone is a trap: a file left behind after logging out
    is a file that looks like a login and is not, and a file deleted without logging
    out leaves the session live on Telegram's side, listed in the account's own
    sessions forever."""
    try:
        client = await _connected(state_dir, authorised=False)
    except TelegramError:
        session_path(state_dir).unlink(missing_ok=True)
        return
    try:
        if await client.is_user_authorized():
            await client.log_out()
    finally:
        await client.disconnect()
    session_path(state_dir).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# signing in, which is three steps and cannot be fewer
# --------------------------------------------------------------------------


@dataclass
class Login:
    """One sign-in in progress, held between the steps of it.

    It has to be held: Telegram answers the phone number with a `phone_code_hash` that
    the code must be sent back WITH, and the connection it was issued on is the one it
    is good for. So the client stays open across the steps, which is why this is an
    object a caller keeps rather than three functions that each connect.

    `step` is what the caller must supply next, and it is the whole protocol: `code`,
    then `password` if the account has two-step on, then `done`."""

    phone: str = ""
    step: str = "phone"
    client: object = None
    hash: str = ""

    @property
    def waiting(self) -> bool:
        return self.step in ("code", "password")

    async def close(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()
            finally:
                self.client = None


async def begin(state_dir: Path, phone: str) -> Login:
    """Ask Telegram to send a code to this number."""
    _, tg_errors = _telethon()
    phone = (phone or "").strip()
    if not phone:
        raise TelegramError("a phone number is needed, with its country code")
    client = await _connected(state_dir, authorised=False)
    if await client.is_user_authorized():
        await client.disconnect()
        raise TelegramError("this machine is already signed in — log out first")
    try:
        sent = await client.send_code_request(phone)
    except tg_errors.PhoneNumberInvalidError:
        await client.disconnect()
        raise TelegramError(f"Telegram does not recognise {phone} as a number")
    except tg_errors.FloodWaitError as e:
        await client.disconnect()
        raise TelegramError(f"Telegram is making us wait {e.seconds}s before asking again")
    except Exception as e:  # noqa: BLE001 — whatever it was, the caller needs the words
        await client.disconnect()
        raise TelegramError(f"Telegram refused to send a code ({type(e).__name__}: {e})")
    return Login(phone=phone, step="code", client=client, hash=sent.phone_code_hash)


async def with_code(login: Login, code: str) -> Login:
    """Hand Telegram the code it sent. Answers `done`, or `password` for two-step."""
    _, tg_errors = _telethon()
    if login.client is None or login.step != "code":
        raise TelegramError("there is no sign-in waiting for a code")
    try:
        await login.client.sign_in(login.phone, (code or "").strip(),
                                   phone_code_hash=login.hash)
    except tg_errors.SessionPasswordNeededError:
        login.step = "password"
        return login
    except tg_errors.PhoneCodeInvalidError:
        raise TelegramError("that code is not the one Telegram sent")
    except tg_errors.PhoneCodeExpiredError:
        await login.close()
        login.step = "phone"
        raise TelegramError("that code has expired — ask for a new one")
    except Exception as e:  # noqa: BLE001
        raise TelegramError(f"Telegram refused the code ({type(e).__name__}: {e})")
    login.step = "done"
    await login.close()
    return login


async def with_password(login: Login, password: str) -> Login:
    """Hand Telegram the two-step password, for an account that has one."""
    _, tg_errors = _telethon()
    if login.client is None or login.step != "password":
        raise TelegramError("there is no sign-in waiting for a password")
    try:
        await login.client.sign_in(password=password or "")
    except tg_errors.PasswordHashInvalidError:
        raise TelegramError("that is not the account's two-step password")
    except Exception as e:  # noqa: BLE001
        raise TelegramError(f"Telegram refused the password ({type(e).__name__}: {e})")
    login.step = "done"
    await login.close()
    return login


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


async def dialogs(state_dir: Path, limit: int = 60) -> list[dict]:
    """The chats this account can see, as something to choose from.

    No messages: browsing is deciding which chat is worth reading, and a hundred chats
    with their history attached is several minutes of requests to answer a question
    about names."""
    client = await _connected(state_dir)
    try:
        out = []
        async for d in client.iter_dialogs(limit=max(1, min(int(limit), 200))):
            out.append({
                "id": int(d.id),
                "title": str(d.name or "").strip() or str(d.id),
                "kind": ("channel" if getattr(d, "is_channel", False)
                         else "group" if getattr(d, "is_group", False) else "private"),
                "unread": int(getattr(d, "unread_count", 0) or 0),
            })
        log.info("telegram: %d dialog(s)", len(out))
        return out
    finally:
        await client.disconnect()


async def _photo(client, sender, into: Path, seen: dict) -> str:
    """Somebody's profile picture, saved once, as a file name under `into`.

    Once per person per read and not once per message, which is the whole reason for
    `seen`: a window of two hundred messages is a dozen people, and a download apiece
    per message would be two hundred requests to Telegram for twelve pictures. Kept on
    disk under the account's own id, so the same person in another chat — and in next
    week's video — is the same file rather than another copy of it.

    A failure is not an error: plenty of accounts have no photo and plenty more hide
    it, and what that costs is the picture, which the initials disc replaces."""
    ident = getattr(sender, "id", None)
    if sender is None or ident is None:
        return ""
    if ident in seen:
        return seen[ident]
    name = f"tg_{ident}.jpg"
    at = into / name
    if at.is_file():
        seen[ident] = name
        return name
    try:
        into.mkdir(parents=True, exist_ok=True)
        got = await client.download_profile_photo(sender, file=str(at))
    except Exception as e:  # noqa: BLE001 — a picture is never worth the read
        log.info("telegram: no picture for %s (%s)", ident, type(e).__name__)
        got = None
    seen[ident] = name if got else ""
    return seen[ident]


async def window(state_dir: Path, chat, limit: int = 200, before: int = 0,
                 photos: Path | None = None,
                 media: Path | None = None) -> tuple[Piece, int, bool]:
    """One window of a chat's messages, oldest first, and how to ask for the one before.

    A chat is ten thousand messages and a video is twenty of them, so there is no such
    thing as reading "the chat" — there is only reading a stretch of it. `before` is a
    message id and means "older than this"; what comes back carries the oldest id in
    the window so the caller can ask for the next one up, and whether there is more
    behind it. Oldest first inside the window because that is the order the
    conversation happened in and the order a video reads it; telethon hands them back
    newest first, which is the order a client SCROLLS and nothing else.

    `chat` is whatever Telegram itself accepts: a numeric id, a `@username`, a link.

    `media` is the media base. Given, each message's picture, sticker, clip or voice is
    downloaded into it (once — the file is named after Telegram's own id for it) and
    its custom emoji are fetched beside it; not given, those messages keep their words
    and a message that had none is skipped, which is what this read did before."""
    client = await _connected(state_dir)
    try:
        try:
            entity = await client.get_entity(_as_target(chat))
        except Exception as e:  # noqa: BLE001
            raise TelegramError(f"no chat answers to {chat!r} ({type(e).__name__})")
        name = str(getattr(entity, "title", "") or _person(entity) or str(chat))
        want = max(1, min(int(limit), 400))
        rows = []
        async for msg in client.iter_messages(entity, limit=want,
                                              offset_id=max(0, int(before))):
            rows.append(msg)
        piece = Piece(title=name, source=f"telegram:{chat}")
        ids: dict[object, int] = {}
        pending: list[tuple[int, object]] = []
        seen_photos: dict = {}
        # the chat's own picture, for the header bar. `download_profile_photo` takes a
        # group and a channel as happily as a person — to Telegram they are all
        # entities with a photo — so the same call that fetches a sender's fetches
        # this, and `seen_photos` keeps it from being fetched twice when the chat is
        # a one-to-one and the other person is also a sender.
        if photos is not None:
            piece.avatar = await _photo(client, entity, photos, seen_photos)
        oldest = 0
        for msg in reversed(rows):
            oldest = min(oldest, msg.id) if oldest else msg.id
            text = str(getattr(msg, "message", "") or "")
            carried = await _media(client, msg, media) if media is not None else None
            if not text.strip() and carried is None:
                continue  # a service line, a poll, a file: nothing to read or draw
            ids[msg.id] = len(piece.lines)
            if getattr(msg, "reply_to_msg_id", None):
                pending.append((len(piece.lines), msg.reply_to_msg_id))
            piece.lines.append(Line(
                who=_sender(msg), text=text, media=carried,
                custom=_custom(msg, text),
                stamp=_clock(msg.date.isoformat() if msg.date else ""),
                day=_day(msg.date.isoformat() if msg.date else ""),
                reactions=_reactions(msg),
                avatar=(await _photo(client, getattr(msg, "sender", None), photos,
                                     seen_photos) if photos is not None else ""),
            ))
        _settle(piece, ids, pending)
        if media is not None:
            # on the connection already open, rather than a second sign-in for the
            # emoji: a window is one conversation with Telegram, not two
            found = await _fetch_emoji(client, doc_ids([piece]), media)
            resolve_custom([piece], found)
        log.info("telegram: %s — %d of %d in this window", name, len(piece.lines), len(rows))
        return piece, oldest, len(rows) >= want
    finally:
        await client.disconnect()


async def history(state_dir: Path, chat, limit: int = 120,
                  photos: Path | None = None, media: Path | None = None) -> list[Piece]:
    """The last stretch of a chat, for a run that is not being watched.

    The blunt answer, and it says so: a run told to read a chat and nothing else takes
    the most recent `limit` messages, because there is nobody there to choose. When
    there IS somebody there, they use :func:`window` and pick (see the chat room),
    which is the honest way and the one the room offers first."""
    piece, _oldest, _more = await window(state_dir, chat, limit, photos=photos, media=media)
    return [piece] if piece.lines else []


def _as_target(chat):
    """What the operator typed, as the kind of thing telethon resolves.

    A numeric string is an id and must be an int or it is looked up as a username; a
    `t.me/name` link is the name inside it; anything else is passed through."""
    text = str(chat).strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if "t.me/" in text:
        tail = text.split("t.me/", 1)[1].strip("/")
        return tail.split("/")[0] or text
    return text


def _person(user) -> str:
    """Somebody's name as it reads on a bubble: what they called themselves, else
    their @username, else their id — never blank, because a blank author draws as an
    empty bubble with nobody above it."""
    if user is None:
        return ""
    bits = [str(getattr(user, "first_name", "") or ""), str(getattr(user, "last_name", "") or "")]
    name = " ".join(b for b in bits if b).strip()
    if name:
        return name
    for attr in ("title", "username"):
        value = str(getattr(user, attr, "") or "").strip()
        if value:
            return value
    ident = getattr(user, "id", "")
    return str(ident) if ident else ""


def _sender(msg) -> str:
    sender = getattr(msg, "sender", None)
    name = _person(sender)
    if name:
        return name
    # a channel post has no sender of its own: it is the channel talking
    chat = getattr(msg, "chat", None)
    return _person(chat) or str(getattr(msg, "sender_id", "") or "")


def _reactions(msg) -> list[tuple[str, int]]:
    """The emoji on a message, in the order Telegram lists them.

    A custom (premium) reaction is a document rather than a character, so it comes
    back as a reference to be fetched (see :func:`resolve_custom`) with 👍 as what it
    falls back to — a client without the pack draws something there too."""
    raw = getattr(msg, "reactions", None)
    out: list[tuple[str, int]] = []
    for r in getattr(raw, "results", None) or []:
        reaction = getattr(r, "reaction", None)
        count = max(1, int(getattr(r, "count", 1) or 1))
        emoticon = getattr(reaction, "emoticon", None)
        doc = getattr(reaction, "document_id", None)
        if emoticon:
            out.append((str(emoticon), count))
        elif doc:
            out.append((custom_reaction(f"doc:{doc}", "👍"), count))
    return out


def _custom(msg, text: str) -> list[tuple[str, str]]:
    """The custom emoji in a message, in order, as (fallback, `doc:<id>`).

    Telegram counts entity offsets in UTF-16 code units, which is not what a Python
    string counts — every emoji outside the basic plane is two of them — so the
    fallback is cut out of the UTF-16 form of the text and not out of the text."""
    out: list[tuple[str, str]] = []
    wide = text.encode("utf-16-le")
    for ent in getattr(msg, "entities", None) or []:
        doc = getattr(ent, "document_id", None)
        if doc is None or type(ent).__name__ != "MessageEntityCustomEmoji":
            continue
        a, n = int(ent.offset) * 2, int(ent.length) * 2
        alt = wide[a:a + n].decode("utf-16-le", errors="ignore").strip()
        if alt:
            out.append((alt, f"doc:{doc}"))
    return out


# What a download is called, by what Telegram says it is: the name says what decoder
# opens it (see `chat.media`), and a `.tgs` saved as `.bin` is a sticker nobody draws.
_MIME_EXT = {"application/x-tgsticker": ".tgs", "video/webm": ".webm",
             "image/webp": ".webp", "video/mp4": ".mp4", "audio/ogg": ".ogg",
             "audio/mpeg": ".mp3", "image/jpeg": ".jpg", "image/png": ".png",
             "image/gif": ".gif", "video/quicktime": ".mov"}
# Past this, the message keeps its words and loses its picture: a chat video shows a
# clip for four seconds, and a two-gigabyte film attached to a message is not worth
# the download to show four seconds of it.
MEDIA_CAP = 60 * 1024 * 1024


async def _media(client, msg, into: Path) -> Media | None:
    """What this message carries, downloaded into the media base. None for nothing, a
    file nobody can draw, or anything too big to be worth fetching.

    Named after Telegram's own id for the file, so the same sticker in forty messages
    — and in next week's read — is one download."""
    kind = ("round" if getattr(msg, "video_note", None) else
            "voice" if getattr(msg, "voice", None) else
            "sticker" if getattr(msg, "sticker", None) else
            "animation" if getattr(msg, "gif", None) else
            "video" if getattr(msg, "video", None) else
            "photo" if getattr(msg, "photo", None) else "")
    doc = getattr(msg, "document", None)
    if not kind and doc is not None and str(getattr(doc, "mime_type", "")).startswith("image/"):
        kind = "photo"  # a picture sent "as a file"
    if not kind:
        return None
    if kind == "photo" and getattr(msg, "photo", None) is not None:
        ident, ext, size = msg.photo.id, ".jpg", 0
    else:
        ident = getattr(doc, "id", None)
        ext = _MIME_EXT.get(str(getattr(doc, "mime_type", "")), "")
        size = int(getattr(doc, "size", 0) or 0)
        if ident is None or not ext:
            return None
    if size > MEDIA_CAP:
        log.info("telegram: message %s carries %d MB — keeping the words only",
                 msg.id, size >> 20)
        return None
    into.mkdir(parents=True, exist_ok=True)
    at = into / f"tg_{ident}{ext}"
    if not at.is_file():
        try:
            await client.download_media(msg, file=str(at))
        except Exception as e:  # noqa: BLE001 — one picture is never worth the read
            log.info("telegram: message %s: no media (%s)", msg.id, type(e).__name__)
            at.unlink(missing_ok=True)
            return None
    emoji = ""
    for attr in getattr(doc, "attributes", None) or []:
        if type(attr).__name__ == "DocumentAttributeSticker":
            emoji = str(getattr(attr, "alt", "") or "")
    w, h, dur, _moves = media_mod.facts(at)
    return Media(kind=kind, path=str(at), w=w, h=h, duration=dur, emoji=emoji,
                 file=at.name)


def doc_ids(pieces) -> set[str]:
    """Every custom emoji id these pieces still have to have fetched."""
    out: set[str] = set()
    for piece in pieces:
        for ln in piece.lines:
            out.update(ref[4:] for _alt, ref in ln.custom if ref.startswith("doc:"))
            for emoji, _n in ln.reactions:
                ref = emoji.partition("|")[0]
                if "doc:" in ref:
                    out.add(ref.split("doc:", 1)[1])
    return out


def resolve_custom(pieces, found: dict[str, str]) -> None:
    """Put fetched files where the ids were — and the plain fallback where nothing
    was fetched, because a reference nobody can draw is worse than the emoji the
    client would have shown."""
    from .exports import split_reaction

    for piece in pieces:
        for ln in piece.lines:
            ln.custom = [(alt, found[ref[4:]]) for alt, ref in ln.custom
                         if not ref.startswith("doc:") or ref[4:] in found]
            out = []
            for emoji, n in ln.reactions:
                alt, ref = split_reaction(emoji)
                if ref.startswith("doc:"):
                    file = found.get(ref[4:])
                    emoji = custom_reaction(file, alt) if file else alt
                out.append((emoji, n))
            ln.reactions = out


def _cached_emoji(ids, into: Path) -> dict[str, str]:
    out = {}
    for ident in ids:
        hit = next(iter(sorted(into.glob(f"ce_{ident}.*"))), None) if into.is_dir() else None
        if hit is not None:
            out[ident] = hit.name
    return out


async def _fetch_emoji(client, ids, into: Path) -> dict[str, str]:
    found = _cached_emoji(ids, into)
    missing = [int(i) for i in ids if i not in found and str(i).isdigit()]
    if not missing:
        return found
    try:
        from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest
    except ImportError:
        return found
    into.mkdir(parents=True, exist_ok=True)
    for k in range(0, len(missing), 100):
        try:
            docs = await client(GetCustomEmojiDocumentsRequest(document_id=missing[k:k + 100]))
        except Exception as e:  # noqa: BLE001 — the fallback emoji is still an emoji
            log.info("telegram: custom emoji not fetched (%s)", type(e).__name__)
            break
        for doc in docs or []:
            ext = _MIME_EXT.get(str(getattr(doc, "mime_type", "")), "")
            if not ext:
                continue
            at = into / f"ce_{doc.id}{ext}"
            try:
                await client.download_media(doc, file=str(at))
                found[str(doc.id)] = at.name
            except Exception as e:  # noqa: BLE001
                log.info("telegram: emoji %s: %s", doc.id, type(e).__name__)
                at.unlink(missing_ok=True)
    return found


async def custom_emoji(state_dir: Path, ids, into: Path) -> dict[str, str]:
    """Fetch custom emoji by id into the media base: `{id: file name}` for those that
    came. What an EXPORT needs, since it names its custom emoji and carries none of
    them. Whatever was fetched before is answered from disk without a connection, and
    no account at all is not an error — the fallback emoji is what gets drawn."""
    ids = {str(i) for i in ids}
    found = _cached_emoji(ids, into)
    if not ids - set(found):
        return found
    try:
        client = await _connected(state_dir)
    except TelegramError as e:
        log.info("telegram: custom emoji stay plain (%s)", e)
        return found
    try:
        return await _fetch_emoji(client, ids, into)
    finally:
        await client.disconnect()
