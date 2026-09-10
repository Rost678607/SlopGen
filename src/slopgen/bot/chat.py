"""The chat half: everything you can do without opening the panel.

The Mini App is the whole program, so this is deliberately not a second copy of it.
It is the part worth having on a phone with one hand free — start something, see what
is going on, and above all HAND MATERIAL OVER, which is the one thing this pipeline
asks of a person at unpredictable hours. A fandom run that wants twelve pictures parks
and waits; answering it from the chat is a reply with a photo attached, and answering
it from the panel is finding a laptop.

What the chat is not is a form. A run has forty settings and a phone has a thumb, so
starting one here means picking a mode and a world and letting every other setting be
what the configs already say — and then, if that was not enough, opening the panel on
the very same run. The two are one program: the same supervisor, the same runs, the
same folders on disk.

Every button and every message is filtered through the allow-list first (see auth.py).
A stranger gets exactly one reply, and it contains their own id and nothing else about
this machine.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigStore
from ..config.models import BotConfig
from .. import labels
from ..media.stock import IMAGE_EXTS, VIDEO_EXTS
from ..pipeline import manual
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.loop import check_params
from ..web.params import drama_params, fandom_params, info_params
from ..web.runs import Supervisor, parked
from .api import UPLOAD_LIMIT, Telegram, TelegramError, button, esc, keyboard, link_button, web_app_button
from .auth import Allowlist

log = logging.getLogger(__name__)

ALLOWED_SUFFIXES = IMAGE_EXTS | VIDEO_EXTS

# How many shots the bot puts in the chat at once when a run parks. A drama can owe two
# hundred pictures, and two hundred messages is not a work queue, it is a denial of
# service against your own notifications. The rest are one button away.
BURST = 6

# What a settled run is called. Reused from the browser's own table rather than written
# again here: the same run must not be "готов" in one place and "закончен" in another.
STATUS_LABEL = {
    "queued": "js.queued", "running": "js.running", "paused": "js.waiting",
    "review": "js.in-review", "done": "js.done", "failed": "js.failed",
    "stopped": "js.stopped",
}
STATUS_ICON = {"queued": "⋯", "running": "⚙", "paused": "🖐", "review": "🔍",
               "done": "✅", "failed": "💥", "stopped": "⏹"}


@dataclass
class Session:
    """One person's place in the conversation.

    In memory only, and that is the right amount of persistence: everything a session
    holds is a half-filled form, and a half-filled form that survives a restart is a
    form nobody remembers starting."""

    chat: int
    step: str = ""  # what a plain text message would answer right now
    draft: dict = field(default_factory=dict)  # the body web/params.py will read
    mode: str = ""
    picks: list[str] = field(default_factory=list)  # what the last keyboard offered
    # the shot the next file answers, as (run id, video folder, shot id)
    ask: tuple[str, str, str] | None = None
    # every shot the bot has named in this chat, by the message that named it — so a
    # reply with a photo attached lands on the right one however far back it was
    ask_msgs: dict[int, tuple[str, str, str]] = field(default_factory=dict)


class Chat:
    """The conversation, and the watch it keeps on the runs it started.

    One instance per bot. Updates arrive on the polling thread and the watch runs on
    its own, which is safe because everything they share is either a plain dict written
    from one side or the supervisor, which was built to be called from run threads."""

    def __init__(self, tg: Telegram, store: ConfigStore, sup: Supervisor,
                 allow: Allowlist, cfg: BotConfig, address=lambda: ""):
        self.tg, self.store, self.sup, self.allow, self.cfg = tg, store, sup, allow, cfg
        self.address = address  # what the panel's public URL is RIGHT NOW, or ""
        self.sessions: dict[int, Session] = {}
        self.homes: dict[str, int] = {}  # run id -> the chat that asked for it
        self.seen: dict[str, str] = {}  # run id -> what it looked like last tick
        self.told: set[int] = set()  # strangers already answered once

    # -- words -------------------------------------------------------------

    def t(self, key: str) -> str:
        """A label, in whatever language the interface is set to. Looked up per call:
        the language is a setting, and the bot outlives every change to it."""
        return labels.t(key, self.store.global_cfg.ui.lang)

    def _session(self, chat: int) -> Session:
        return self.sessions.setdefault(chat, Session(chat=chat))

    # -- the front door ----------------------------------------------------

    def handle(self, update: dict) -> None:
        """One update. Anything that goes wrong is reported to the person who caused
        it and never to the poller: a bot that dies on a malformed message is a bot
        that anybody can turn off."""
        message = update.get("message")
        query = update.get("callback_query")
        try:
            if query:
                self._query(query)
            elif message:
                self._message(message)
        except TelegramError as e:
            log.warning("bot: Telegram refused: %s", e)
        except Exception:
            log.exception("bot: update %s blew up", update.get("update_id"))
            chat = ((message or query.get("message") or {}).get("chat") or {}).get("id")
            if chat:
                self.tg.send(chat, f"💥 {esc(self.t('bot.blew-up'))}")

    def _welcome(self, who: dict | None, chat: int) -> bool:
        """True when this person may be served. A stranger is answered once, with
        their own id — which is the only thing they need and the only thing worth
        telling them: they cannot learn from it that anything else is here."""
        user = int((who or {}).get("id", 0))
        if self.allow.allows(user):
            return True
        if user and user not in self.told:
            self.told.add(user)
            self.tg.send(chat, f"{esc(self.t('bot.no-entry'))}\n<code>{user}</code>")
            log.warning("bot: refused %s (@%s)", user, (who or {}).get("username", "?"))
        return False

    # -- messages ----------------------------------------------------------

    def _message(self, msg: dict) -> None:
        chat = msg["chat"]["id"]
        if not self._welcome(msg.get("from"), chat):
            return
        if any(k in msg for k in ("photo", "video", "document", "animation", "video_note")):
            return self._material(chat, msg)
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            return self._command(chat, text)
        if text:
            return self._typed(chat, text)

    def _command(self, chat: int, text: str) -> None:
        word = text.split()[0].lstrip("/").split("@")[0]
        rest = text[len(text.split()[0]):].strip()
        if word in ("start", "menu"):
            self.sessions.pop(chat, None)
            self._menu(chat, greeting=True)
        elif word in ("panel", "app", "web"):
            self._panel(chat)
        elif word == "new":
            self._modes(chat)
        elif word == "runs":
            self._runs(chat)
        elif word == "id":
            self.tg.send(chat, f"{esc(self.t('bot.id.yours'))} <code>{chat}</code>")
        elif word == "help":
            self.tg.send(chat, self._help())
        elif word == "say" and rest:  # the same as typing, for a client that insists
            self._typed(chat, rest)
        else:
            self._menu(chat)

    def _typed(self, chat: int, text: str) -> None:
        """A plain line of text, which means whatever the session last asked for."""
        s = self._session(chat)
        if s.step == "idea":
            s.draft["idea"] = text
            return self._launch(chat)
        if s.step == "scenario":
            s.draft["scenario"] = text
            return self._launch(chat)
        self._menu(chat)

    # -- the menu ----------------------------------------------------------

    def _panel_row(self) -> list[dict]:
        url = self.address()
        return web_app_button(f"🖥 {self.t('bot.menu.panel')}", url)

    def _menu(self, chat: int, greeting: bool = False) -> None:
        url = self.address()
        head = self.t("bot.hi") if greeting else self.t("bot.menu.what")
        note = "" if url else f"\n\n<i>{esc(self.t('bot.panel.none'))}</i>"
        self.tg.send(chat, f"<b>slopgen</b>\n{esc(head)}{note}", keyboard(
            self._panel_row(),
            [button(f"⛏ {self.t('bot.menu.new')}", "new"),
             button(f"⏵ {self.t('bot.menu.runs')}", "runs")],
            [link_button(f"🔗 {self.t('bot.menu.link')}", url)] if url else [],
        ))

    def _panel(self, chat: int) -> None:
        url = self.address()
        if not url:
            return self.tg.send(chat, f"🕳 {esc(self.t('bot.panel.none'))}")
        self.tg.send(chat, f"🖥 <b>{esc(self.t('bot.panel.here'))}</b>\n<code>{esc(url)}</code>",
                     keyboard(self._panel_row(), [link_button(self.t("bot.menu.link"), url)]))

    def _help(self) -> str:
        return "\n".join([f"<b>slopgen</b>", esc(self.t("bot.help")), "",
                          "/new — " + esc(self.t("bot.menu.new")),
                          "/runs — " + esc(self.t("bot.menu.runs")),
                          "/panel — " + esc(self.t("bot.menu.panel")),
                          "/id — " + esc(self.t("bot.id.yours"))])

    # -- starting something ------------------------------------------------

    def _modes(self, chat: int) -> None:
        s = self._session(chat)
        s.step, s.draft, s.mode = "", {}, ""
        self.tg.send(chat, esc(self.t("bot.mode.pick")), keyboard(
            [button(self.t("web.mode.fandom"), "m:fandom")],
            [button(self.t("web.mode.info"), "m:info")],
            [button(self.t("web.mode.drama"), "m:drama")],
        ))

    def _pick(self, chat: int, message: int, step: str, names: list[str],
              prompt: str, empty: str) -> None:
        """Offer a list of config names as buttons, by INDEX.

        The index and not the name, because callback data is capped at 64 bytes and a
        world may be called anything at all — including something that does not fit,
        or does not survive a round trip through a URL-safe field."""
        s = self._session(chat)
        s.picks, s.step = names, step
        if not names:
            return self._say(chat, message, esc(empty))
        rows = [[button(esc(n)[:60], f"p:{i}")] for i, n in enumerate(names[:20])]
        self._say(chat, message, esc(prompt), keyboard(*rows, [button("← " + self.t("bot.menu.back"), "new")]))

    def _say(self, chat: int, message: int | None, text: str, markup: dict | None = None) -> None:
        """Redraw the message a button was pressed on, or send a new one.

        A wizard that grows downwards is four dead screens by the time it starts
        anything; one that redraws in place is one screen that changes."""
        if message:
            self.tg.edit(chat, message, text, markup)
        else:
            self.tg.send(chat, text, markup)

    def _chose(self, chat: int, message: int, index: int) -> None:
        """The nth button of the list last offered was pressed."""
        s = self._session(chat)
        if not (0 <= index < len(s.picks)):
            return self._modes(chat)
        chosen, step = s.picks[index], s.step
        if step == "world":
            s.draft["fandom"] = chosen
            s.step = "scenario"
            self._say(chat, message,
                      f"🌍 <b>{esc(chosen)}</b>\n{esc(self.t('bot.scenario.ask'))}",
                      keyboard([button(f"🎲 {self.t('bot.topic.any')}", "go")]))
        elif step == "content":
            s.draft["content_type"] = chosen
            s.step = "idea"
            self._say(chat, message,
                      f"📰 <b>{esc(chosen)}</b>\n{esc(self.t('bot.topic.ask'))}",
                      keyboard([button(f"🎲 {self.t('bot.topic.any')}", "go")]))
        elif step == "orchestration":
            s.draft["orchestration"] = chosen
            s.step = "scenario"
            self._say(chat, message,
                      f"🎬 <b>{esc(chosen)}</b>\n{esc(self.t('bot.scenario.ask'))}",
                      keyboard([button(f"🎲 {self.t('bot.topic.any')}", "go")]))

    def _mode(self, chat: int, message: int, mode: str) -> None:
        s = self._session(chat)
        s.mode, s.draft, s.step = mode, {"lang": self.store.global_cfg.ui.lang}, ""
        if mode == "fandom":
            self._pick(chat, message, "world", sorted(self.store.fandoms),
                       self.t("bot.world.pick"), self.t("bot.world.none"))
        elif mode == "info":
            self._pick(chat, message, "content", sorted(self.store.content_types),
                       self.t("bot.content.pick"), self.t("bot.content.none"))
        else:
            self._pick(chat, message, "orchestration", sorted(self.store.orchestrations),
                       self.t("bot.orch.pick"), self.t("bot.orch.none"))

    def _launch(self, chat: int, message: int | None = None) -> None:
        """Turn the draft into a run, through the same functions the browser uses.

        The settings this form does not ask about are not defaulted here — they are
        defaulted in `web/params.py`, once, for both frontends. What the chat adds is
        nothing at all, which is the point."""
        s = self._session(chat)
        build = {"fandom": fandom_params, "info": info_params, "drama": drama_params}
        if s.mode not in build:
            return self._modes(chat)
        try:
            params = build[s.mode](self.store, s.draft)
        except Exception as e:
            detail = getattr(e, "detail", None) or str(e)
            return self._say(chat, message, f"⚠️ {esc(detail)}")
        # the same check the loop does before it commits to a hundred videos: a name no
        # config has would otherwise fail hours in, at a stage nobody is watching
        problems = check_params(self.store, params)
        if problems:
            return self._say(chat, message, "⚠️ " + esc("; ".join(problems)))
        title = s.draft.get("scenario") or s.draft.get("idea") or self.t(f"web.mode.{s.mode}")
        run = self.sup.start(params, title=str(title)[:80])
        self.homes[run.id] = chat
        self.seen[run.id] = self._signature(run)
        s.step, s.draft = "", {}
        self._say(chat, message,
                  f"⚙ <b>{esc(self.t('bot.started'))}</b>\n{esc(run.title)}",
                  keyboard([button(f"⏵ {self.t('bot.run.open')}", f"r:{run.id}")],
                           self._panel_row()))

    # -- buttons -----------------------------------------------------------

    def _query(self, q: dict) -> None:
        chat = q["message"]["chat"]["id"]
        message = q["message"]["message_id"]
        if not self._welcome(q.get("from"), chat):
            return self.tg.answer(q["id"])
        data = q.get("data", "")
        self.tg.answer(q["id"])
        head, _, arg = data.partition(":")
        if data == "new":
            self._modes(chat)
        elif data == "runs":
            self._runs(chat, message)
        elif data == "menu":
            self._menu(chat)
        elif data == "go":
            self._launch(chat, message)
        elif head == "m":
            self._mode(chat, message, arg)
        elif head == "p":
            self._chose(chat, message, int(arg or -1))
        elif head == "r":
            self._card(chat, arg, message)
        elif head in ("rs", "rv", "ra", "rr", "rk", "rm"):
            self._act(chat, head, arg, message)
        elif head == "a":
            run_id, _, n = arg.partition(":")
            self._aim(chat, run_id, int(n or 0))

    # -- runs --------------------------------------------------------------

    def _runs(self, chat: int, message: int | None = None) -> None:
        runs = sorted(self.sup.runs.values(),
                      key=lambda r: -max(r.started_at, r.finished_at))[:10]
        if not runs:
            return self._say(chat, message, esc(self.t("js.no-runs-yet")),
                             keyboard([button(f"⛏ {self.t('bot.menu.new')}", "new")]))
        rows = [[button(f"{STATUS_ICON.get(r.status, '·')} {esc(r.title)[:40]}", f"r:{r.id}")]
                for r in runs]
        self._say(chat, message, f"<b>{esc(self.t('bot.menu.runs'))}</b>",
                  keyboard(*rows, self._panel_row()))

    def _card(self, chat: int, run_id: str, message: int | None = None) -> None:
        """One run, and only the buttons that would actually do something to it."""
        run = self.sup.runs.get(run_id)
        if run is None:
            return self._runs(chat, message)
        park = parked(run) if run.status not in ("running", "queued") else {}
        lines = [f"{STATUS_ICON.get(run.status, '·')} <b>{esc(run.title)}</b>",
                 f"{esc(self.t(STATUS_LABEL.get(run.status, 'js.running')))}"
                 f" · {esc(run.params.mode)}"
                 + (f" · {esc(run.params.fandom)}" if run.params.fandom else "")]
        if run.message:
            lines.append(f"<i>{esc(self.t(run.message) if run.message.startswith('js.') else run.message)}</i>")
        if run.progress:
            unit, done, total = run.progress
            lines.append(f"{esc(unit)}: {done}/{total}")
        if park.get("asks"):
            lines.append(f"🖐 {esc(self.t('js.pictures-missing'))} {park['asks']}")
        if park.get("review_stage"):
            lines.append(f"🔍 {esc(self.t('js.sitting-at'))}{esc(park['review_stage'])}"
                         f"{esc(self.t('js.waiting-on-you'))}")
        rows = []
        if run.status in ("running", "queued"):
            rows.append([button(f"⏹ {self.t('js.stop-it')}", f"rs:{run.id}")])
        if park.get("asks"):
            rows.append([button(f"🖐 {self.t('js.give-it-pictures')} {park['asks']}", f"ra:{run.id}")])
        if park.get("review_stage"):
            rows.append([button(f"▶ {self.t('bot.run.goon')}", f"rk:{run.id}")])
        if run.status in ("paused", "review", "stopped", "failed"):
            rows.append([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")])
        if park.get("video"):
            rows.append([button(f"🎬 {self.t('js.watch-the-video')}", f"rv:{run.id}")])
        rows.append([button(f"⏵ {self.t('bot.menu.runs')}", "runs")])
        self._say(chat, message, "\n".join(lines), keyboard(*rows))

    def _act(self, chat: int, what: str, run_id: str, message: int | None) -> None:
        run = self.sup.runs.get(run_id)
        if run is None:
            return self._runs(chat, message)
        self.homes.setdefault(run.id, chat)
        if what == "rs":
            self.sup.stop(run.id)
            self.tg.send(chat, f"⏹ {esc(self.t('js.stopped'))}: {esc(run.title)}")
        elif what == "rv":
            self._deliver(chat, run)
        elif what == "ra":
            self._show_asks(chat, run)
        elif what == "rm":
            self._show_asks(chat, run, more=True)
        elif what == "rr":
            self._resume(chat, run)
        elif what == "rk":
            self._go_on(chat, run)
        self._card(chat, run_id, message)

    def _resume(self, chat: int, run) -> None:
        if run.run_dir is None:
            return self.tg.send(chat, f"⚠️ {esc(self.t('bot.run.nofolder'))}")
        self.sup.resume(run.run_dir)
        self.seen[run.id] = self._signature(run)
        self.tg.send(chat, f"↻ {esc(self.t('js.resuming'))}: {esc(run.title)}")

    def _go_on(self, chat: int, run) -> None:
        """Release a breakpoint without editing anything.

        Reviewing IS editing, and editing a script through a chat keyboard is a worse
        version of the panel — so the chat offers only the half it can do honestly:
        release it, or open the panel and actually read it. Nothing is applied, so the
        stage's output is not stale and nothing is re-run."""
        if run.run_dir is None:
            return self.tg.send(chat, f"⚠️ {esc(self.t('bot.run.nofolder'))}")
        cp = Checkpoint.load(run.run_dir)
        released = 0
        for i in range(run.params.count):
            stage = cp.review_stage(i)
            job = cp.load_job(i) if stage else None
            if job is None:
                continue
            cp.review_done(job, cp.completed(i), stage, rerun=False)
            released += 1
        if released:
            self.sup.resume(run.run_dir)
            self.seen[run.id] = self._signature(run)
        self.tg.send(chat, f"▶ {esc(self.t('js.changes-applied-the-run-goes-on'))}"
                     if released else f"⚠️ {esc(self.t('bot.run.nothing-parked'))}")

    def _deliver(self, chat: int, run) -> None:
        """Put the finished cut in the chat, which is the whole reason for the chat.

        Telegram refuses uploads over 50 MB from a bot, and a two-minute filtered cut
        can exceed that — so the ceiling produces a link into the panel rather than a
        failed send with no explanation."""
        cut = self._cut(run)
        if cut is None:
            return self.tg.send(chat, f"🕳 {esc(self.t('bot.video.none'))}")
        limit = min(self.cfg.max_upload_mb * 1024 * 1024, UPLOAD_LIMIT)
        size = cut.stat().st_size
        if size > limit:
            url = self.address()
            markup = keyboard(self._panel_row()) if url else None
            return self.tg.send(chat, f"🎬 <b>{esc(run.title)}</b>\n"
                                f"{esc(self.t('bot.video.big'))} — {size / 1048576:.0f} MB\n"
                                f"<code>{esc(str(cut))}</code>", markup)
        self.tg.send_file(chat, cut, caption=f"🎬 <b>{esc(run.title)}</b>", kind="video")

    def _cut(self, run) -> Path | None:
        if run.run_dir is None:
            return None
        cuts = sorted(run.run_dir.glob("*/*.mp4"), key=lambda p: p.stat().st_mtime)
        return cuts[-1] if cuts else None

    # -- material ----------------------------------------------------------

    def _asks(self, run) -> list[tuple[str, str, str, str]]:
        """Every shot still owed, as (video folder, shot id, want, prompt).

        Read off the manifest on disk, exactly as the panel and `slopgen gather` read
        it: the manifest is the authority and this process is not, so a picture dropped
        into the inbox by hand disappears from this list without the bot being told.

        A file already in the inbox counts as owed no longer, even though the manifest
        still says pending — the manifest is only rewritten when the run picks the
        inbox up, which is minutes away and possibly never. Without this the bot asks
        again for the very shot it just thanked you for."""
        out = []
        if run.run_dir is None or not run.run_dir.is_dir():
            return out
        for work in sorted(p for p in run.run_dir.iterdir() if p.is_dir()):
            path = manual.manifest_path(work)
            if not path.is_file():
                continue
            mf = manual.ManualManifest.model_validate_json(path.read_text(encoding="utf-8"))
            inbox = manual.inbox_dir(work)
            for sh in mf.shots:
                if sh.status == "delivered" or any(inbox.glob(f"{sh.id}.*")):
                    continue
                out.append((work.name, sh.id, sh.want, manual.task_text(sh)))
        return out

    def _show_asks(self, chat: int, run, more: bool = False) -> None:
        """Name the shots, one message each, so a reply can point at one of them."""
        s = self._session(chat)
        pending = self._asks(run)
        if not pending:
            return self.tg.send(chat, f"✅ {esc(self.t('bot.ask.done'))}",
                                keyboard([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")]))
        shown = pending if more else pending[:BURST]
        self.tg.send(chat, f"🖐 <b>{esc(run.title)}</b>\n"
                     f"{esc(self.t('bot.ask.head'))} {len(pending)}\n"
                     f"<i>{esc(self.t('bot.ask.how'))}</i>")
        for video, shot_id, want, prompt in shown:
            sent = self.tg.send(chat, f"<b>{esc(shot_id)}</b> · {esc(video)} · "
                                f"{esc(self.t('gather.want.' + want) if want in ('photo', 'video') else want)}\n"
                                f"{esc(prompt)[:3500]}")
            s.ask_msgs[sent["message_id"]] = (run.id, video, shot_id)
        s.ask = (run.id, shown[0][0], shown[0][1])
        rows = []
        if len(pending) > len(shown):
            rows.append([button(f"… {self.t('bot.ask.more')} {len(pending) - len(shown)}",
                                f"rm:{run.id}")])
        rows.append([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")])
        self.tg.send(chat, esc(self.t("bot.ask.aim")) + f" <b>{esc(shown[0][1])}</b>",
                     keyboard(*rows, self._panel_row()))

    def _aim(self, chat: int, run_id: str, index: int) -> None:
        run = self.sup.runs.get(run_id)
        pending = self._asks(run) if run else []
        if not (0 <= index < len(pending)):
            return
        self._session(chat).ask = (run_id, pending[index][0], pending[index][1])
        self.tg.send(chat, esc(self.t("bot.ask.aim")) + f" <b>{esc(pending[index][1])}</b>")

    def _file_of(self, msg: dict) -> tuple[str, str]:
        """The file id and the extension it should land under, or ("", "").

        The extension decides how the pipeline treats what arrived — a still is held
        and panned to length, a clip plays — so it is taken from what Telegram says
        the file IS, never from a caption or a name somebody typed."""
        if msg.get("photo"):  # sorted smallest first; the last is the full-size one
            return msg["photo"][-1]["file_id"], ".jpg"
        for key in ("video", "animation", "video_note"):
            if msg.get(key):
                return msg[key]["file_id"], ".mp4"
        doc = msg.get("document")
        if doc:
            suffix = Path(str(doc.get("file_name", ""))).suffix.lower()
            if suffix in ALLOWED_SUFFIXES:
                return doc["file_id"], suffix
            # a client that sent no usable name: ask Telegram what it stored
            suffix = self.tg.remote_suffix(doc["file_id"])
            if suffix in ALLOWED_SUFFIXES:
                return doc["file_id"], suffix
        return "", ""

    def _material(self, chat: int, msg: dict) -> None:
        """A photo or a clip arrived. Work out which shot it answers, and file it.

        Two ways to say which: reply to the message that named the shot, or send it
        plain and it answers whichever shot the bot last pointed at. The reply is the
        reliable one and the plain one is what people actually do, so both work and
        the bot always says out loud which shot it took it for."""
        s = self._session(chat)
        reply = (msg.get("reply_to_message") or {}).get("message_id")
        target = s.ask_msgs.get(reply) if reply else None
        target = target or s.ask
        if target is None:
            return self.tg.send(chat, f"🤷 {esc(self.t('bot.ask.which'))}",
                                keyboard([button(f"⏵ {self.t('bot.menu.runs')}", "runs")]))
        run_id, video, shot_id = target
        run = self.sup.runs.get(run_id)
        if run is None or run.run_dir is None:
            s.ask = None
            return self.tg.send(chat, f"⚠️ {esc(self.t('bot.run.nofolder'))}")
        file_id, suffix = self._file_of(msg)
        if not file_id:
            return self.tg.send(chat, f"⚠️ {esc(self.t('bot.ask.wrong-kind'))}")
        inbox = manual.inbox_dir(run.run_dir / video)
        dest = inbox / f"{shot_id}{suffix}"
        # A second file for the same shot replaces the first: the manifest matches by
        # stem, so two extensions for one id would be two answers to one question.
        for old in inbox.glob(f"{shot_id}.*"):
            if old != dest:
                old.unlink(missing_ok=True)
        self.tg.download(file_id, dest)
        # and recorded in the manifest at once, the way the browser panel and the
        # gather screen record it. The manifest is what every screen counts and what
        # the resume reads; leaving it to the resume is what made a run go on saying
        # it was short of a picture that was already lying in its inbox.
        work = run.run_dir / video
        mf = manual.ManualManifest.load(work)
        shot = mf.by_id(shot_id)
        if shot is not None and manual._valid_asset(dest):
            manual.attach(shot, dest)
            mf.save(work)
        log.info("bot: %s -> %s", shot_id, dest)
        self._advance(chat, run, shot_id, self._asks(run))

    def _advance(self, chat: int, run, done_id: str, left: list) -> None:
        """Say what landed and point at the next thing, which is the whole loop."""
        s = self._session(chat)
        if not left:
            s.ask = None
            return self.tg.send(chat, f"✅ <b>{esc(done_id)}</b> · {esc(self.t('bot.ask.done'))}",
                                keyboard([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")],
                                         self._panel_row()))
        s.ask = (run.id, left[0][0], left[0][1])
        self.tg.send(chat, f"✅ <b>{esc(done_id)}</b> · {esc(self.t('bot.ask.left'))} {len(left)}\n"
                     f"<b>{esc(left[0][1])}</b>: {esc(left[0][3])[:600]}",
                     keyboard([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")]))

    # -- the watch ---------------------------------------------------------

    def _signature(self, run) -> str:
        """What a run looks like from outside, in one comparable string.

        Status alone is not enough: a run that delivers its eleventh picture and keeps
        waiting has not changed status, and it is exactly then that there is something
        to say."""
        if run.status in ("running", "queued"):
            return run.status
        park = parked(run)
        return f"{run.status}:{park['review_stage']}:{park['asks']}"

    def watch(self, stop: threading.Event) -> None:
        """Notice what happened while nobody was looking, and say so.

        Polling rather than subscribing, and deliberately: the supervisor's event
        stream is per-run and live, while this has to cover runs adopted off disk at
        startup and runs started from the panel by somebody else's browser. A stat call
        per run every few seconds buys a bot that is never the last to know."""
        while not stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("bot: the watch stumbled")
            stop.wait(max(1.0, self.cfg.poll_s))

    def _tick(self) -> None:
        for run in list(self.sup.runs.values()):
            now = self._signature(run)
            was = self.seen.get(run.id)
            self.seen[run.id] = now
            # First sight is never news. At startup the supervisor adopts every run on
            # disk, and announcing forty finished videos from last week is how a bot
            # gets muted on its first day.
            if was is None or was == now:
                continue
            self._report(run, was)

    def _report(self, run, was: str) -> None:
        chat = self.homes.get(run.id) or self.allow.owner()
        if not chat:
            return
        status = run.status
        if status in ("running", "queued", "stopped"):
            return  # asked for, or already answered where it was asked for
        park = parked(run)
        if status == "done":
            self.tg.send(chat, f"✅ <b>{esc(run.title)}</b> · {esc(self.t('js.done'))}")
            if self.cfg.deliver_video:
                try:
                    self._deliver(chat, run)
                except TelegramError as e:
                    self.tg.send(chat, f"⚠️ {esc(e.description)}")
        elif status == "failed":
            self.tg.send(chat, f"💥 <b>{esc(run.title)}</b>\n<i>{esc(run.message)[:800]}</i>",
                         keyboard([button(f"↻ {self.t('js.resume')}", f"rr:{run.id}")],
                                  self._panel_row()))
        elif park.get("asks"):
            self._show_asks(chat, run)
        elif park.get("review_stage"):
            self.tg.send(chat, f"🔍 <b>{esc(run.title)}</b>\n"
                         f"{esc(self.t('js.sitting-at'))}{esc(park['review_stage'])}"
                         f"{esc(self.t('js.waiting-on-you'))}",
                         keyboard([button(f"▶ {self.t('bot.run.goon')}", f"rk:{run.id}")],
                                  self._panel_row()))

    # -- the address moved -------------------------------------------------

    def announce(self, url: str) -> None:
        """Tell the owner where the panel is now.

        This is the entire cost of a disposable address, and it is one message: the
        button in every menu is built from whatever the tunnel currently says, so
        nothing else in the bot has to care that it changed."""
        chat = self.allow.owner()
        if not chat:
            log.warning("bot: the panel is at %s, but the allow-list is empty", url)
            return
        try:
            self.tg.send(chat, f"🖥 <b>{esc(self.t('bot.panel.moved'))}</b>\n<code>{esc(url)}</code>",
                         keyboard(self._panel_row(), [link_button(self.t("bot.menu.link"), url)]))
        except TelegramError as e:
            log.warning("bot: could not announce the address: %s", e)
