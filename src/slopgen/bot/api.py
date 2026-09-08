"""The Telegram Bot API, in as much of it as this bot uses.

A hand-written client rather than a framework, and the reason is the shape of the
program around it. slopgen's runs are threads in a pool with a blocking supervisor;
an async bot framework would have made the bot the thing that owns the process and
everything else a guest in its event loop. This is a few hundred lines of `httpx.post`
that runs on a thread of its own and calls the supervisor the way a terminal would —
so the pipeline stays the centre of the program and the chat stays a frontend.

Long polling, not a webhook. A webhook needs the tunnel to be up before Telegram can
reach the bot at all, which would make "the chat works" depend on the very thing the
chat is there to tell you about when it breaks.
"""

from __future__ import annotations

import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# What a bot may upload over the HTTP API, whatever the file actually is. Telegram's
# own limit; a longer video has to be fetched from the panel instead.
UPLOAD_LIMIT = 50 * 1024 * 1024


class TelegramError(RuntimeError):
    """Telegram said no. `code` is its own, not HTTP's."""

    def __init__(self, method: str, code: int, description: str):
        super().__init__(f"{method}: {description} ({code})")
        self.method, self.code, self.description = method, code, description


def keyboard(*rows: list[dict]) -> dict:
    """An inline keyboard, as `[[button, button], [button]]`.

    Buttons are made by :func:`button` and :func:`web_app_button`; this only wraps
    them, so a row of nothing (a Mini App button with no address yet) collapses
    instead of sending Telegram an empty row it will refuse."""
    return {"inline_keyboard": [r for r in rows if r]}


def button(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def link_button(text: str, url: str) -> dict:
    return {"text": text, "url": url}


def web_app_button(text: str, url: str) -> list[dict]:
    """The Mini App button — a row, because it is often the only thing in one.

    Telegram opens `web_app` buttons only over HTTPS, and refuses the whole message
    if the URL is anything else. So a missing or plain-http address produces no
    button at all rather than a message that fails to send."""
    return [{"text": text, "web_app": {"url": url}}] if url.startswith("https://") else []


@dataclass
class Telegram:
    """One bot's connection. Not thread-safe on purpose: one poller, one thread."""

    token: str
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self._http = httpx.Client(timeout=httpx.Timeout(self.timeout, read=self.timeout + 30))
        self._base = f"https://api.telegram.org/bot{self.token}"

    def close(self) -> None:
        self._http.close()

    # -- the wire ----------------------------------------------------------

    def call(self, method: str, files: dict | None = None, **params) -> dict:
        """One API call. Nested structures go as JSON, which is what Telegram wants
        for `reply_markup` in a multipart request and accepts everywhere else."""
        import json as _json

        payload = {k: (_json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                   for k, v in params.items() if v is not None}
        r = self._http.post(f"{self._base}/{method}", data=payload, files=files)
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(method, r.status_code, r.text[:200])
        if not body.get("ok"):
            raise TelegramError(method, int(body.get("error_code", 0)),
                                str(body.get("description", "")))
        return body.get("result")

    def me(self) -> dict:
        return self.call("getMe")

    def updates(self, offset: int, timeout: int = 25) -> list[dict]:
        """Long-poll. A timeout here is the normal case, not a failure: it means
        nobody said anything for 25 seconds."""
        try:
            return self.call("getUpdates", offset=offset, timeout=timeout,
                             allowed_updates=["message", "callback_query"]) or []
        except httpx.TimeoutException:
            return []

    # -- saying things -----------------------------------------------------

    def send(self, chat: int, text: str, markup: dict | None = None,
             reply_to: int | None = None, preview: bool = False) -> dict:
        """Send HTML. Telegram's parser is strict about tags and forgiving about
        nothing, so anything interpolated into a message goes through :func:`esc`."""
        return self.call("sendMessage", chat_id=chat, text=text[:4096], parse_mode="HTML",
                         reply_markup=markup, reply_to_message_id=reply_to,
                         link_preview_options={"is_disabled": not preview})

    def edit(self, chat: int, message: int, text: str,
             markup: dict | None = None) -> dict | None:
        """Redraw a message in place. "not modified" is the one error worth
        swallowing: pressing the button you are already on is not a mistake."""
        try:
            return self.call("editMessageText", chat_id=chat, message_id=message,
                             text=text[:4096], parse_mode="HTML", reply_markup=markup,
                             link_preview_options={"is_disabled": True})
        except TelegramError as e:
            if "not modified" in e.description:
                return None
            raise

    def answer(self, callback_id: str, text: str = "", alert: bool = False) -> None:
        """Acknowledge a button. Telegram spins the button until this arrives, so it
        is sent even when there is nothing to say — and its own failure is never worth
        aborting the work the button asked for."""
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id,
                      text=text[:200] or None, show_alert=alert or None)
        except (TelegramError, httpx.HTTPError):
            pass

    # -- files -------------------------------------------------------------

    def send_file(self, chat: int, path: Path, caption: str = "",
                  kind: str = "document", markup: dict | None = None) -> dict:
        """Upload a local file. `kind` is video | photo | audio | document.

        A video sent as a document plays nowhere and previews as a paperclip; one sent
        as a video is watchable in the chat, which for this program is the entire point
        of the chat existing."""
        method = {"video": "sendVideo", "photo": "sendPhoto",
                  "audio": "sendAudio"}.get(kind, "sendDocument")
        field = {"video": "video", "photo": "photo",
                 "audio": "audio"}.get(kind, "document")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            return self.call(method, files={field: (path.name, fh, mime)},
                             chat_id=chat, caption=(caption or None), parse_mode="HTML",
                             supports_streaming=True if kind == "video" else None,
                             reply_markup=markup)

    def download(self, file_id: str, dest: Path) -> Path:
        """Fetch what somebody sent, to a path we chose.

        The name is ours, never theirs: a Telegram file name is attacker-controlled
        text, and the one place this lands is a run's inbox where the name IS the
        instruction about which shot it answers."""
        info = self.call("getFile", file_id=file_id)
        remote = info["file_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._http.stream("GET", f"https://api.telegram.org/file/bot{self.token}/{remote}") as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(64 * 1024):
                    fh.write(chunk)
        return dest

    def remote_suffix(self, file_id: str) -> str:
        """What Telegram calls the file, reduced to its extension.

        Only the extension survives, and it decides how the pipeline treats what
        arrived: a `.jpg` is a still to be held and panned, a `.mp4` is a clip."""
        try:
            return Path(self.call("getFile", file_id=file_id)["file_path"]).suffix.lower()
        except (TelegramError, httpx.HTTPError, KeyError):
            return ""


def esc(text: str) -> str:
    """HTML-escape. Everything that came from a person or a config goes through this:
    a world called `<example>` should not be able to break a message, or to write one."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
