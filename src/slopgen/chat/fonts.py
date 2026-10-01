"""Finding a typeface by NAME, never by path.

Pillow wants a file; a config that held one would be a config that works on the
machine it was written on. The deploy target is Debian and the development machine is
NixOS, where every font lives under a hashed store path that changes when anything is
updated — so a path written down anywhere is a path that breaks.

So the skins ask for a family (`"Roboto"`, `"IBM Plex Sans"`) and fontconfig answers,
exactly as the subtitle style already does: `SubtitlesConfig.font` has always been a
NAME, handed to libass, which resolves it the same way. What fontconfig does when the
family is absent is the other half of why this is safe — it substitutes something of
the same shape rather than failing, so a machine without Discord's stand-in draws the
chat in whatever grotesque it does have, and the video is a little off rather than
absent.

The one thing that cannot be left to fontconfig is colour emoji. Noto's emoji font is
a bitmap font with ONE strike in it (109px), and Pillow refuses any other size
outright — so emoji are drawn at that size into a scratch image and scaled, which is
what :func:`emoji_font` exists to make obvious at the call site.
"""

from __future__ import annotations

import logging
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

log = logging.getLogger(__name__)

# The single size Noto Color Emoji carries a bitmap strike for. Not a choice.
EMOJI_PX = 109

# Asked for when a skin names nothing, and when fontconfig itself comes up empty.
# DejaVu because it is the one face this project already guarantees — it is in
# `shell.nix` and in the server's apt line for the subtitles.
FALLBACK = "DejaVu Sans"


@lru_cache(maxsize=64)
def find(family: str, weight: str = "regular") -> Path | None:
    """The file fontconfig gives for this family, or None if there is no fontconfig.

    `weight` is passed through as a fontconfig pattern rather than resolved here,
    because which file carries the bold of a family is exactly the question
    fontconfig exists to answer — and on a variable font the answer is the same file,
    which Pillow then has to be told to instance (see :func:`load`)."""
    pattern = f"{family}:weight={weight}" if weight != "regular" else family
    try:
        out = subprocess.run(
            ["fc-match", pattern, "-f", "%{file}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("fc-match is not usable (%s) — falling back to whatever Pillow finds", e)
        return None
    path = Path(out.stdout.strip())
    return path if path.is_file() else None


@lru_cache(maxsize=256)
def load(family: str, size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """One face at one size, cached — a conversation asks for the same six a thousand
    times.

    A variable font resolves to a single file for every weight, so asking fontconfig
    for the bold and then drawing with the file it returns would draw the regular.
    Pillow can instance one by name, and the names are not standardised, so several
    spellings are tried and a miss costs the weight rather than the render."""
    path = find(family, weight) or find(FALLBACK, weight)
    font = (ImageFont.truetype(str(path), size) if path
            else ImageFont.load_default(size))
    if weight != "regular" and path is not None:
        _instance(font, weight)
    return font


def _instance(font: ImageFont.FreeTypeFont, weight: str) -> None:
    """Point a variable font at a named instance, quietly doing nothing if it is not
    one (`get_variation_names` raises on a static face, which is the ordinary case)."""
    wanted = {"bold": (b"Bold", b"SemiBold", b"Medium"),
              "medium": (b"Medium", b"SemiBold", b"Bold")}.get(weight, ())
    try:
        have = font.get_variation_names()
    except Exception:  # noqa: BLE001 — a static font, which is not an error
        return
    for name in wanted:
        if name in have:
            try:
                font.set_variation_by_name(name)
            except Exception:  # noqa: BLE001
                pass
            return


@lru_cache(maxsize=1)
def emoji_font() -> ImageFont.FreeTypeFont | None:
    """Noto Color Emoji at its one legal size, or None where it is not installed.

    None is a usable answer: the caller draws the emoji with the text face instead
    and gets the black-and-white glyph, which is wrong-looking but not missing."""
    path = find("Noto Color Emoji")
    if path is None or "emoji" not in path.name.lower():
        return None
    try:
        return ImageFont.truetype(str(path), EMOJI_PX)
    except OSError as e:
        log.warning("the colour emoji font at %s will not load (%s)", path, e)
        return None
