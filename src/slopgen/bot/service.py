"""The process: a chat, a web server and a tunnel, in one place on purpose.

Three parts could have been three services. They are one because they share the thing
that matters — the supervisor. A run started by a button in Telegram and a run started
in the Mini App are the SAME object in the SAME list: stopping it from the chat stops
what the panel is showing, and a run that parks for pictures says so in both places at
once. Two processes would have been two lists of runs that disagree about reality, and
the disagreement would have surfaced as "I pressed stop and nothing happened".

The layout inside the process follows from what blocks. Uvicorn wants an event loop, so
it gets a thread; the supervisor's runs are already threads in a pool; the watch that
notices a finished video is a third; and long polling — which spends most of its life
in a 25-second read — sits in the main thread, where Ctrl-C reaches it.

Nothing here is a daemon in the unix sense. Detaching is `--detach`, which spawns this
same command with its output in a log file and its own session, and on a server none of
that is used at all: systemd runs it in the foreground and owns it (see deploy/).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from ..config import ConfigStore
from .api import Telegram, TelegramError
from .auth import Allowlist, TelegramAuth
from .chat import Chat
from .tunnel import Tunnel

log = logging.getLogger(__name__)


class BotError(RuntimeError):
    """Something the operator has to fix before the bot can start at all."""


def state_dir(store: ConfigStore) -> Path:
    d = Path(store.global_cfg.paths.state)
    d.mkdir(parents=True, exist_ok=True)
    return d


def pid_file(store: ConfigStore) -> Path:
    return state_dir(store) / "bot.pid"


def url_file(store: ConfigStore) -> Path:
    """Where the current public address is written.

    On disk rather than only in memory because the address is the one piece of state
    somebody outside this process wants: `slopgen bot --status` from another terminal,
    a deploy script reporting where the panel went, you at three in the morning."""
    return state_dir(store) / "bot.url"


# -- running ----------------------------------------------------------------


def serve(store: ConfigStore) -> None:
    """Run the bot in the foreground until it is asked to stop."""
    cfg = store.global_cfg.bot
    token = (os.environ.get(cfg.token_env) or "").strip()
    if not token:
        raise BotError(f"no {cfg.token_env} in the environment or .env — "
                       f"make a bot with @BotFather and put its token there")

    allow = Allowlist(Path(cfg.allow_file))
    allow.ensure_file()
    if not allow.ids():
        log.warning("bot: %s lets nobody in yet. Write your Telegram id into it — "
                    "the bot will tell you what it is if you message it", cfg.allow_file)

    tg = Telegram(token)
    try:
        me = tg.me()
    except (TelegramError, httpx.HTTPError) as e:
        raise BotError(f"Telegram would not talk to that token: {e}")
    log.info("bot: signed in as @%s", me.get("username", "?"))

    # The app exists whether or not it is served: it owns the supervisor, and the chat
    # is a frontend to that supervisor before it is anything to do with HTTP.
    from ..web.app import create_app

    auth = TelegramAuth(token, allow)
    app = create_app(store, bound=store.global_cfg.web.host,
                     bound_port=store.global_cfg.web.port, tg=auth)
    sup = app.state.supervisor

    stop = threading.Event()
    address = {"url": cfg.public_url.rstrip("/")}
    chat = Chat(tg, store, sup, allow, cfg, address=lambda: address["url"])

    server = _web_thread(app, store) if cfg.web else None
    if not cfg.web:
        # nobody will run the startup hook that does this, and a bot that cannot see
        # yesterday's parked runs is a bot that cannot be asked about them
        sup.adopt_all(Path(store.global_cfg.paths.output))

    tunnel = None
    if cfg.web and cfg.tunnel == "cloudflared" and not cfg.public_url:
        tunnel = _raise_tunnel(store, cfg, address, chat)
    _remember(store, address["url"])

    watcher = threading.Thread(target=chat.watch, args=(stop,), name="slopgen-watch",
                               daemon=True)
    watcher.start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    where = address["url"] or f"http://{store.global_cfg.web.host}:{store.global_cfg.web.port}"
    log.info("bot: up. Panel: %s", where or "(none)")
    try:
        _poll(tg, chat, stop)
    finally:
        log.info("bot: shutting down")
        stop.set()
        if tunnel:
            tunnel.stop()
        if server:
            server.should_exit = True
        sup.shutdown()
        tg.close()
        url_file(store).unlink(missing_ok=True)
        pid_file(store).unlink(missing_ok=True)


def _poll(tg: Telegram, chat: Chat, stop: threading.Event) -> None:
    """Long-poll for updates until stopped.

    The backlog is dropped on the way in. Telegram keeps undelivered updates for a day,
    and replaying them means every button pressed while the bot was down fires the
    moment it comes back — which for buttons that START RUNS is not a quirk."""
    offset = 0
    try:
        recent = tg.call("getUpdates", offset=-1, timeout=0) or []
        if recent:
            offset = recent[-1]["update_id"] + 1
            log.info("bot: dropped %d update(s) from while it was down", len(recent))
    except (TelegramError, httpx.HTTPError) as e:
        log.warning("bot: could not clear the backlog: %s", e)

    quiet = 0.0
    while not stop.is_set():
        try:
            updates = tg.updates(offset, timeout=25)
            quiet = 0.0
        except (TelegramError, httpx.HTTPError) as e:
            # The network, or Telegram, or a token that stopped working. None of them
            # is fatal and none of them is worth a hot loop, so back off and say so once.
            quiet = min(quiet * 2 or 5.0, 120.0)
            log.warning("bot: no updates (%s) — retrying in %.0fs", e, quiet)
            stop.wait(quiet)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            if stop.is_set():
                break
            chat.handle(update)


def _web_thread(app, store: ConfigStore):
    """Serve the panel on a thread, without letting it own the process.

    `install_signal_handlers=False` is the whole point: uvicorn normally takes SIGINT
    for itself, and a Ctrl-C would then stop the web server while the chat carried on
    answering buttons about a panel that no longer exists."""
    import uvicorn

    cfg = store.global_cfg.web
    # The tunnel connects from this machine, so loopback is enough and is the right
    # default: the public address is Cloudflare's, and nothing needs a port open here.
    config = uvicorn.Config(app, host=cfg.host, port=cfg.port, log_level="warning")
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, name="slopgen-web", daemon=True).start()
    for _ in range(100):  # a few seconds; the tunnel is pointless before this is up
        if server.started:
            break
        time.sleep(0.1)
    log.info("bot: panel served on http://%s:%d", cfg.host, cfg.port)
    return server


def _raise_tunnel(store: ConfigStore, cfg, address: dict, chat: Chat) -> Tunnel | None:
    """Open the public door, and keep the address that comes back current everywhere."""
    def moved(url: str) -> None:
        first = not address["url"]
        address["url"] = url
        _remember(store, url)
        if not first:  # the first address is in the log and in every menu already
            chat.announce(url)

    tunnel = Tunnel(store.global_cfg.web.port, cfg.cloudflared, on_url=moved,
                    host=store.global_cfg.web.host)
    if not tunnel.available():
        log.error("bot: no %r on PATH — the chat works, the Mini App button does not. "
                  "Install cloudflared or set [bot].public_url", cfg.cloudflared)
        return None
    tunnel.start()
    tunnel.wait(30)
    return tunnel


def _remember(store: ConfigStore, url: str) -> None:
    try:
        if url:
            url_file(store).write_text(url + "\n", encoding="utf-8")
        else:
            url_file(store).unlink(missing_ok=True)
    except OSError as e:
        log.warning("bot: could not write %s: %s", url_file(store), e)


# -- not holding the terminal ----------------------------------------------


def detach(store: ConfigStore, extra: list[str] | None = None) -> int:
    """Start the bot in its own session, with its output in a log, and return the pid.

    A generation run is hours long and a bot that watches one has to outlive the
    terminal it was started from — but not by becoming something you need `systemctl`
    to see. This is the small version: a child in its own process group, a pid file, a
    log file. The server does it properly (deploy/slopgen-bot.service)."""
    running = alive(store)
    if running:
        raise BotError(f"already running as pid {running} — `slopgen bot --stop` first")
    state = state_dir(store)
    log_path = state / "bot.log"
    handle = open(log_path, "ab", buffering=0)
    handle.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} starting ===\n".encode())
    proc = subprocess.Popen([sys.executable, "-m", "slopgen", "bot", *(extra or [])],
                            stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                            start_new_session=True, cwd=os.getcwd())
    # Wait long enough to catch the failures that happen before anything is serving —
    # no token, a token Telegram refuses, a port already taken. Reporting "running,
    # pid 12345" about a process that died two seconds later is worse than not
    # detaching at all: the terminal comes back and nothing is there.
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        pid_file(store).write_text(f"{proc.pid}\n", encoding="utf-8")
        return proc.pid
    raise BotError(f"it stopped at once (exit {proc.returncode}). Last of {log_path}:\n"
                   + _tail(log_path))


def _tail(path: Path, lines: int = 8) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                          .splitlines()[-lines:])
    except OSError:
        return "(no log)"


def alive(store: ConfigStore) -> int:
    """The pid of the detached bot, or 0. A pid file whose process is gone is cleaned
    up here rather than believed: a stale one would make `--detach` refuse forever."""
    path = pid_file(store)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    try:
        os.kill(pid, 0)
    except OSError:
        path.unlink(missing_ok=True)
        return 0
    return pid


def halt(store: ConfigStore, timeout: float = 15.0) -> bool:
    """Ask the detached bot to stop, and wait for it to actually be gone."""
    pid = alive(store)
    if not pid:
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            pid_file(store).unlink(missing_ok=True)
            return True
        time.sleep(0.25)
    os.kill(pid, signal.SIGKILL)
    pid_file(store).unlink(missing_ok=True)
    return True


def current_url(store: ConfigStore) -> str:
    try:
        return url_file(store).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
