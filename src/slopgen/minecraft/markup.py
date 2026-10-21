"""The markup of a fandom's world, read and written wherever it is.

With the game open on the world, through the game — so an edit appears in the world
at once and the mod never reads half a file; with the game closed, straight from the
save. Both the CLI and the browser go through here.
"""

from __future__ import annotations

from . import paths
from .client import DEFAULT_PORT, Game, GameUnavailable
from .registry import Registry


def game_on(mc, port: int = DEFAULT_PORT) -> dict | None:
    """The game's hello when it has THIS world open, else None."""
    try:
        with Game(port=port, timeout=2.0) as g:
            h = g.hello()
    except GameUnavailable:
        return None
    if h.get("in_world") and (not getattr(mc, "world", "") or h.get("world") == mc.world):
        return h
    return None


def load_registry(mc, port: int = DEFAULT_PORT) -> tuple[Registry, str]:
    """The markup, from the game if it has this world open, else from the save."""
    if game_on(mc, port):
        try:
            with Game(port=port, timeout=5.0) as g:
                return Registry.model_validate(g.call("registry.get")), "game"
        except GameUnavailable:
            pass
    return Registry.load(paths.save_dir(mc)), "save"


def save_registry(mc, reg: Registry, port: int = DEFAULT_PORT) -> str:
    if game_on(mc, port):
        try:
            with Game(port=port, timeout=5.0) as g:
                g.call("registry.put", {"registry": reg.model_dump(mode="json")})
                return "game"
        except GameUnavailable:
            pass
    reg.save(paths.save_dir(mc))
    return "save"
