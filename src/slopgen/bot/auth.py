"""Who the bot talks to, and how the Mini App knows it is them.

Two doors, one list. The chat door checks the sender's id against the allow-list; the
Mini App door checks a signature Telegram put on the page's own `initData` and then
checks the same list. Neither can be opened by knowing a URL, which matters because
the URL is a public address on the open internet and the thing behind it starts runs
that spend real money.

The list is a plain text file rather than a config key for one reason: it is the thing
most likely to be edited by somebody who is not editing anything else. One id per line,
`#` for a comment, re-read whenever the file changes — so adding a person is an edit,
not a restart, and the bot does not have to be trusted to write its own guest list.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

TEMPLATE = """\
# Кто может говорить с ботом. Один Telegram-id в строке; всё после # — комментарий.
# Первый id в файле — владелец: ему приходят уведомления, которых никто не просил
# (адрес панели переехал, прогон упал). Свой id можно спросить у бота: /id.
#
# Who the bot talks to. One Telegram id per line; everything after # is a comment.
# The FIRST id is the owner — notices nobody asked for go there. Ask the bot for
# your own id with /id.
"""


@dataclass
class Allowlist:
    """The guest list, re-read whenever the file changes.

    A missing file is not an error and not an open door: it is written out with the
    explanation in it and lets nobody in, which is the state a fresh install should
    be in. Everything is stale by design — the file is the authority, this is a cache
    with a stat call in front of it."""

    path: Path
    _stamp: float = -1.0
    _ids: list[int] = field(default_factory=list)

    def ids(self) -> list[int]:
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            if self._stamp != -1.0:  # it was there and now is not: say so once
                log.warning("bot: %s is gone — nobody is allowed in", self.path)
            self._stamp, self._ids = -1.0, []
            return []
        if stamp == self._stamp:
            return self._ids
        found: list[int] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            entry = line.split("#", 1)[0].strip()
            if not entry:
                continue
            try:
                found.append(int(entry))
            except ValueError:
                log.warning("bot: %s: %r is not a Telegram id, skipping", self.path, entry)
        self._stamp, self._ids = stamp, found
        log.info("bot: %d id(s) allowed", len(found))
        return found

    def allows(self, user_id: int) -> bool:
        return user_id in self.ids()

    def owner(self) -> int | None:
        """The first id on the list. Notices with no addressee go here — a tunnel that
        moved, a run that failed with nobody watching."""
        found = self.ids()
        return found[0] if found else None

    def ensure_file(self) -> None:
        """Write the template if there is no file, so the first thing to do is obvious.

        Deliberately not "add the first person who writes to the bot": a bot whose
        guest list fills itself from whoever knocks first has no guest list."""
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(TEMPLATE, encoding="utf-8")
        log.warning("bot: wrote an empty allow-list at %s — nobody can talk to the bot "
                    "until an id is in it", self.path)


@dataclass
class TelegramAuth:
    """Verify the `initData` a Mini App is opened with.

    Telegram hands the page a query string signed with a key derived from the bot
    token, so checking it proves both that Telegram produced it and that it was
    produced for THIS bot. That is the whole of authentication for the Mini App —
    there is no password to type, which is the point of opening it from a phone.

    `max_age_s` is not paranoia about replay so much as about a link that got shared:
    `initData` in a forwarded URL keeps working until it expires, and a day is long
    enough for a session and short enough to be worth having."""

    token: str
    allow: Allowlist
    max_age_s: int = 24 * 3600

    def verify(self, init_data: str) -> int | None:
        """The Telegram user id, or None for anything that does not check out."""
        if not init_data or not self.token:
            return None
        pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
        data = dict(pairs)
        given = data.pop("hash", "")
        if not given:
            return None
        check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, given):
            log.warning("bot: a Mini App sign-in did not carry Telegram's signature")
            return None
        try:
            age = time.time() - float(data.get("auth_date", 0))
        except ValueError:
            return None
        if age > self.max_age_s:
            log.info("bot: a Mini App sign-in was %.0f h old, refusing", age / 3600)
            return None
        try:
            who = int(json.loads(data.get("user", "{}"))["id"])
        except (ValueError, KeyError, TypeError):
            return None
        if not self.allow.allows(who):
            log.warning("bot: %s signed in with a good signature but is not on the list", who)
            return None
        return who
