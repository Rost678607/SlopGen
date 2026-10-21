"""The line to the game: the `slopcam` mod's control socket.

Newline-delimited JSON over 127.0.0.1 (see `slopcam` `ControlServer`). A request is
`{"id", "method", "params"}` and gets exactly one `{"id", "ok", "result" | "error"}`;
whatever the game says unprompted — the world changed, the markup changed, a take
progressed — arrives as `{"event", "data"}` and is handed to the listeners.

The game is the operator's and they start it; slopgen only connects. So "the game is
not running" is an ordinary answer here (`GameUnavailable`), not a crash: a run that
needs the game parks and says so, the way a run that needs a picture does.
"""

from __future__ import annotations

import itertools
import json
import socket
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

DEFAULT_PORT = 25599


class GameError(RuntimeError):
    """The game answered, and the answer was no."""


class GameUnavailable(GameError):
    """Nothing is listening: the game is not running, or slopcam is not installed in it."""


class Game:
    def __init__(self, port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout: float = 5.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock: socket.socket | None = None
        self._pending: dict[int, Future] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, Any], None]] = []
        self._reader: threading.Thread | None = None

    # -- connection ----------------------------------------------------------

    def connect(self) -> "Game":
        if self._sock is not None:
            return self
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise GameUnavailable(
                f"the game is not listening on {self.host}:{self.port} — start Minecraft with "
                f"the slopcam mod and open the world") from e
        s.settimeout(None)
        self._sock = s
        self._reader = threading.Thread(target=self._read, name="slopcam-client", daemon=True)
        self._reader.start()
        return self

    def close(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
        for f in self._pending.values():
            if not f.done():
                f.set_exception(GameUnavailable("the connection to the game closed"))
        self._pending.clear()

    def __enter__(self) -> "Game":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def on_event(self, fn: Callable[[str, Any], None]) -> None:
        self._listeners.append(fn)

    # -- calls ---------------------------------------------------------------

    def call(self, method: str, params: dict | None = None, timeout: float | None = None) -> Any:
        """One request, waited for. `timeout` None = the client's default; a take that
        is being played needs a long one."""
        self.connect()
        rid = next(self._ids)
        fut: Future = Future()
        self._pending[rid] = fut
        line = json.dumps({"id": rid, "method": method, "params": params or {}}, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self._sock.sendall(line.encode("utf-8"))
            except OSError as e:
                self._pending.pop(rid, None)
                self.close()
                raise GameUnavailable("the connection to the game broke") from e
        try:
            return fut.result(timeout=timeout if timeout is not None else self.timeout)
        except TimeoutError as e:
            self._pending.pop(rid, None)
            raise GameError(f"the game did not answer {method!r} in time") from e

    def _read(self) -> None:
        buf = b""
        sock = self._sock
        try:
            while sock is not None:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(json.loads(line.decode("utf-8")))
        except (OSError, ValueError):
            pass
        finally:
            if self._sock is sock:
                self.close()

    def _dispatch(self, msg: dict) -> None:
        if "event" in msg:
            for fn in list(self._listeners):
                try:
                    fn(msg["event"], msg.get("data"))
                except Exception:  # a listener's bug is not the connection's
                    pass
            return
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        if msg.get("ok"):
            fut.set_result(msg.get("result"))
        else:
            fut.set_exception(GameError(str(msg.get("error") or "the game said no")))

    # -- conveniences ----------------------------------------------------------

    def hello(self) -> dict:
        return self.call("hello")
