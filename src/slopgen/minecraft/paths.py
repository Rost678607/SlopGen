"""Where a fandom's Minecraft world is on disk: the Prism instance, its mods, the save.

The markup lives in the save, so slopgen reads it straight from disk when the game is
closed — which is how the browser can show and edit the markup of a world nobody is
playing — and through the game's socket when it is open, so an edit appears in the
world at once.
"""

from __future__ import annotations

from pathlib import Path

PRISM_DEFAULT = Path.home() / ".local" / "share" / "PrismLauncher"


class WorldNotConfigured(LookupError):
    pass


def prism_root(mc) -> Path:
    return Path(mc.prism_dir).expanduser() if getattr(mc, "prism_dir", "") else PRISM_DEFAULT


def instance_dir(mc) -> Path:
    """The game directory of the instance: Prism calls it `minecraft/` (older MultiMC
    instances `.minecraft/`)."""
    if not getattr(mc, "instance", ""):
        raise WorldNotConfigured("the fandom does not say which Prism instance its world is in")
    root = prism_root(mc) / "instances" / mc.instance
    for name in ("minecraft", ".minecraft"):
        if (root / name).is_dir():
            return root / name
    return root / "minecraft"


def save_dir(mc) -> Path:
    if not getattr(mc, "world", ""):
        raise WorldNotConfigured("the fandom does not say which world it is built in")
    return instance_dir(mc) / "saves" / mc.world


def mods_dir(mc) -> Path:
    return instance_dir(mc) / "mods"


def repo_mod_dir() -> Path:
    """`minecraft/slopcam/` in the repository this package was installed from."""
    return Path(__file__).resolve().parents[3] / "minecraft" / "slopcam"
