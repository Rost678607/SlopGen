"""Finding a font FILE for a font NAME, on whatever machine this happens to be.

A config that held a path would be a config that works on the machine it was written
on: the deploy target is Debian, the development machine is NixOS — where every font
lives under a hashed store path that changes when anything is updated — and the third
machine is somebody's Windows laptop. So everything in this project names a family
(`"DejaVu Sans"`, `"Roboto"`) and the name is resolved here, at the last moment.

Three answers are tried, in this order:

1. `$SLOPGEN_FONTS` — directories the operator names, searched before anything else.
   This is the escape hatch for every case below: drop the .ttf in, name the folder,
   and the family resolves the same on every platform.
2. `fc-match` — the right answer wherever it exists, because it is the same resolver
   libass and ffmpeg's `drawtext` use, so what the subtitles get and what Pillow draws
   agree. Linux, the BSDs, and macOS with fontconfig installed.
3. The platform's own font directories, scanned by file name. This is what Windows and
   a bare macOS have instead of fontconfig, and it is why :data:`SUBSTITUTES` exists —
   nothing on Windows is called "DejaVu Sans", so the families this project asks for
   have to be mapped onto the ones a Windows install actually carries.

Returning None is a legitimate answer at every layer. fontconfig substitutes rather
than fails and so does this module, but when there is nothing at all the caller draws
with whatever default it has: a little off, rather than absent.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

#: Directories to search first, `os.pathsep`-separated, from the environment.
ENV_DIRS = "SLOPGEN_FONTS"

EXTENSIONS = (".ttf", ".otf", ".ttc", ".otc")

# What to ask for when the family itself is not installed. Tried in order, and a name
# that is absent simply misses — so one table serves every platform: "Segoe UI" is
# there for Windows, "Helvetica Neue" for macOS, "Liberation Sans" for a Linux box
# without fontconfig. The faces are stand-ins by SHAPE, which is the same bargain
# fontconfig makes: a grotesque for a grotesque, so the picture stays legible.
SUBSTITUTES: dict[str, tuple[str, ...]] = {
    "dejavu sans": ("Segoe UI", "Arial", "Helvetica Neue", "Helvetica", "Liberation Sans"),
    "roboto": ("Segoe UI", "Helvetica Neue", "Arial", "Liberation Sans"),
    "inter": ("Segoe UI", "Helvetica Neue", "Arial", "Liberation Sans"),
    "ibm plex sans": ("Segoe UI", "Helvetica Neue", "Arial", "Liberation Sans"),
    # Colour emoji: the one family with no visual stand-in, only the other vendors'.
    # "seguiemj" is in there as a FILE name rather than a family: Windows abbreviates
    # its own font files and nothing in `C:\Windows\Fonts` is called segoeuiemoji.ttf.
    "noto color emoji": ("Segoe UI Emoji", "seguiemj", "Apple Color Emoji",
                         "Twemoji Mozilla"),
}

# The suffixes a weight wears in a file name. Windows ships `segoeuib.ttf` and
# `arialbd.ttf`; everyone else ships `-Bold`. All of them are tried, and the bare
# family is tried last, because a variable font carries every weight in one file.
_WEIGHT_SUFFIXES: dict[str, tuple[str, ...]] = {
    "regular": ("", "regular", "-regular", "-rg"),
    "medium": ("medium", "-medium", "md", "semibold", "-semibold", "", "regular"),
    "bold": ("bold", "-bold", "b", "bd", "-bd", "semibold", "-semibold", ""),
}


def _norm(text: str) -> str:
    """A name reduced to what two spellings of it have in common."""
    return "".join(c for c in text.lower() if c.isalnum())


def _env_dirs() -> tuple[Path, ...]:
    raw = os.environ.get(ENV_DIRS, "")
    return tuple(Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip())


def _system_dirs() -> tuple[Path, ...]:
    """Where this platform keeps its fonts."""
    system = platform.system()
    if system == "Windows":
        win = Path(os.environ.get("WINDIR", r"C:\Windows"))
        local = os.environ.get("LOCALAPPDATA")
        dirs = [win / "Fonts"]
        if local:  # a per-user install, which is what you get without admin rights
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        return tuple(dirs)
    if system == "Darwin":
        return (Path("/System/Library/Fonts"),
                Path("/System/Library/Fonts/Supplemental"),
                Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts")
    return (Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            Path.home() / ".local" / "share" / "fonts", Path.home() / ".fonts")


@lru_cache(maxsize=32)
def _index(directory: Path) -> dict[str, Path]:
    """Every font file under `directory`, by normalised file stem.

    Cached per directory because this walks the tree, and the tree is `/usr/share/fonts`
    on a machine that has no `fc-match` to walk it for us. A directory that does not
    exist indexes as empty, which is the answer anyway."""
    found: dict[str, Path] = {}
    try:
        if not directory.is_dir():
            return found
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() in EXTENSIONS and path.is_file():
                found.setdefault(_norm(path.stem), path)
    except OSError as e:  # an unreadable font directory is not an error worth raising
        log.debug("cannot index fonts in %s (%s)", directory, e)
    return found


def _by_filename(family: str, weight: str, dirs: tuple[Path, ...]) -> Path | None:
    """The file whose NAME says it is this family at this weight."""
    stem = _norm(family)
    wanted = [stem + _norm(suffix) for suffix in _WEIGHT_SUFFIXES.get(weight, ("",))]
    for directory in dirs:
        index = _index(directory)
        for key in wanted:
            hit = index.get(key)
            if hit is not None:
                return hit
    return None


@lru_cache(maxsize=64)
def _fontconfig(family: str, weight: str) -> Path | None:
    """What `fc-match` says, or None where there is no fontconfig to ask.

    `weight` goes into the pattern rather than being resolved here, because which file
    carries the bold of a family is exactly the question fontconfig exists to answer —
    and on a variable font the answer is the same file for every weight, which Pillow
    then has to be told to instance (see `chat.fonts.load`)."""
    pattern = f"{family}:weight={weight}" if weight != "regular" else family
    try:
        out = subprocess.run(
            ["fc-match", pattern, "-f", "%{file}"],
            capture_output=True, text=True, timeout=5, check=False,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("no usable fc-match (%s) — looking in the platform's font directories", e)
        return None
    path = Path(out.stdout.strip())
    return path if out.stdout.strip() and path.is_file() else None


@lru_cache(maxsize=128)
def find(family: str, weight: str = "regular") -> Path | None:
    """The file to draw `family` with at `weight`, or None if this machine has nothing.

    A family that is absent falls through to :data:`SUBSTITUTES` rather than to None,
    so the answer on a Windows box without a single one of this project's typefaces
    installed is still a sane grotesque."""
    if Path(family).suffix.lower() in EXTENSIONS:
        # A path where a name was expected. Nothing in the repo writes one, but an
        # operator who edits `[subtitles].font` by hand might, and honouring it costs
        # one branch — while ignoring it would look like the setting does nothing.
        direct = Path(family).expanduser()
        if direct.is_file():
            return direct
    env = _env_dirs()
    for name in (family, *SUBSTITUTES.get(family.lower(), ())):
        hit = (_by_filename(name, weight, env) if env else None)
        if hit is None:
            hit = _fontconfig(name, weight)
        if hit is None:
            hit = _by_filename(name, weight, _system_dirs())
        if hit is not None:
            return hit
    return None


def is_colour_emoji(path: Path) -> bool:
    """Whether this file is somebody's colour emoji font.

    Asked of the NAME, because the alternative is parsing the font's tables to look
    for a colour strike — and every vendor's emoji font says so in its file name:
    `NotoColorEmoji.ttf`, `seguiemj.ttf`, `Apple Color Emoji.ttc`."""
    name = path.name.lower()
    return "emoji" in name or "emj" in name
