"""The Telegram front door: a chat, and the browser UI inside it as a Mini App.

Four files and one idea. `service` is the process — a web server, a tunnel and a
polling loop sharing one supervisor; `chat` is everything you can do without opening
the panel; `auth` is the guest list and the Mini App's signature check; `tunnel` is the
free public address that makes the Mini App possible at all.

Nothing in `pipeline/`, `config/` or `media/` knows this exists, exactly as nothing in
them knows about the terminal or the browser. This is a third consumer of the same
supervisor and the same folders on disk.
"""

from .service import BotError, alive, current_url, detach, halt, serve

__all__ = ["BotError", "alive", "current_url", "detach", "halt", "serve"]
