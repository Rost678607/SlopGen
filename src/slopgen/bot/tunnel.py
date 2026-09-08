"""A public HTTPS address for a server that has no business having one.

Telegram will only open a Mini App over HTTPS, and it has to be an address Telegram's
own servers can reach — which a machine under a desk, or a VPS with no domain and no
certificate, does not have. Cloudflare gives one away: `cloudflared tunnel --url` opens
an outbound connection and hands back a `*.trycloudflare.com` name that terminates TLS
at Cloudflare and forwards to a local port. No account, no domain, no certificate, no
inbound port.

The catch is the address is disposable: every restart of the tunnel is a new name. That
is only a problem for a URL somebody has to remember, and nobody here does — the button
is rebuilt from whatever the tunnel currently says, and the owner is told when it moves.
So the churn costs one message a restart, which is the whole reason this is worth using
instead of a domain.

Nothing about the rest of the bot depends on this file. Set `[bot].public_url` to your
own address and the tunnel is never raised; the button, the links and the sign-in all
work the same, because they only ever ask "what is the address right now".
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

# What cloudflared prints, once, in the middle of a box drawn out of plus signs.
QUICK_URL = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# How long to wait before raising it again after it died, and the ceiling. A tunnel
# that cannot start (no binary, no network) would otherwise be a hot loop against
# Cloudflare, and the thing it is failing to reach is usually the network itself.
BACKOFF_S = 5.0
BACKOFF_MAX_S = 300.0


class Tunnel:
    """A cloudflared quick tunnel, kept alive on a thread of its own.

    `on_url` is called with the new address whenever there is one, including after a
    restart that produced a different one. It is never called with the same address
    twice, so a caller may treat every call as news."""

    def __init__(self, port: int, binary: str = "cloudflared",
                 on_url: Callable[[str], None] | None = None,
                 host: str = "127.0.0.1"):
        self.port, self.binary, self.host = port, binary, host
        self.on_url = on_url or (lambda url: None)
        self.url = ""
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- the public bit ----------------------------------------------------

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._keep_up, name="slopgen-tunnel",
                                        daemon=True)
        self._thread.start()

    def wait(self, seconds: float = 30.0) -> str:
        """Block until there is an address, or give up and return "".

        Worth waiting for exactly once, at startup: the first message the bot sends
        should already carry the button, and a tunnel takes a few seconds to open."""
        deadline = time.monotonic() + seconds
        while not self.url and time.monotonic() < deadline and not self._stop.is_set():
            time.sleep(0.25)
        return self.url

    def stop(self) -> None:
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # -- the thread --------------------------------------------------------

    def _keep_up(self) -> None:
        wait = BACKOFF_S
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._once()
            except FileNotFoundError:
                log.error("bot: no %r on PATH — no Mini App address. Install cloudflared, "
                          "or set [bot].public_url to your own https address", self.binary)
                return
            except Exception as e:  # a tunnel that dies must not take the bot with it
                log.warning("bot: tunnel: %s", e)
            if self._stop.is_set():
                return
            # A tunnel that ran for a while and then dropped is a network hiccup and
            # deserves an immediate retry; one that dies instantly is misconfigured
            # and deserves to be asked less often.
            wait = BACKOFF_S if time.monotonic() - started > 60 else min(wait * 2, BACKOFF_MAX_S)
            log.info("bot: tunnel closed, reopening in %.0fs", wait)
            self.url = ""
            self._stop.wait(wait)

    def _once(self) -> None:
        """Run cloudflared until it exits, watching what it says for an address."""
        cmd = [self.binary, "tunnel", "--no-autoupdate", "--url",
               f"http://{self.host}:{self.port}"]
        log.info("bot: raising a tunnel: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, errors="replace", bufsize=1)
        self._proc = proc
        assert proc.stderr is not None
        for line in proc.stderr:
            if self._stop.is_set():
                break
            found = QUICK_URL.search(line)
            if found and found.group(0) != self.url:
                self.url = found.group(0)
                log.info("bot: the panel is at %s", self.url)
                try:
                    self.on_url(self.url)
                except Exception:  # noqa: BLE001 — telling somebody is not the tunnel's job
                    log.exception("bot: could not announce the new address")
        proc.wait()
