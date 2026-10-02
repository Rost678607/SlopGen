"""Loading a typeface for Pillow, by NAME, never by path.

Where the file comes from is :mod:`slopgen.typeface`'s problem — fontconfig where there
is one, the platform's font directories where there is not. What is left here is the
Pillow half: one face per (family, size, weight), cached, with a variable font pointed
at the right instance and colour emoji loaded at a size the font will actually accept.

So the skins ask for a family (`"Roboto"`, `"IBM Plex Sans"`) and something answers,
exactly as the subtitle style already does: `SubtitlesConfig.font` has always been a
NAME, handed to libass, which resolves it the same way. What happens when the family is
absent is the other half of why this is safe — a face of the same shape is substituted
rather than the render failing, so a machine without Discord's stand-in draws the chat
in whatever grotesque it does have, and the video is a little off rather than absent.

The one thing that cannot be left to a resolver is the SIZE of a colour emoji font.
Noto's is a bitmap font with one strike in it (109px) and Pillow refuses any other size
outright; Apple's has its own strikes; Segoe UI Emoji is vector and takes any size at
all. So the size that worked is remembered and published as :func:`emoji_px`, and emoji
are drawn at it into a scratch image and scaled — which is what :func:`emoji_font`
exists to make obvious at the call site.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from .. import typeface

log = logging.getLogger(__name__)

# The size Noto Color Emoji carries its one bitmap strike at. Not a choice — but not
# every emoji font's answer either, which is what `emoji_px` is for.
EMOJI_PX = 109

# Tried in order when EMOJI_PX is refused: Apple's strikes, then a couple of small
# ones. A vector emoji font (Segoe UI Emoji) accepts the first and never gets here.
EMOJI_FALLBACK_PX = (160, 137, 128, 96, 64)

# Asked for when a skin names nothing, and when the family it names cannot be found.
# DejaVu because it is the one face this project already guarantees — it is in
# `shell.nix` and in the server's apt line for the subtitles.
FALLBACK = "DejaVu Sans"


def find(family: str, weight: str = "regular") -> Path | None:
    """The file this family resolves to here, or None. See :mod:`slopgen.typeface`."""
    return typeface.find(family, weight)


@lru_cache(maxsize=256)
def load(family: str, size: int, weight: str = "regular") -> ImageFont.FreeTypeFont:
    """One face at one size, cached — a conversation asks for the same six a thousand
    times.

    A variable font resolves to a single file for every weight, so asking for the bold
    and then drawing with the file that comes back would draw the regular. Pillow can
    instance one by name, and the names are not standardised, so several spellings are
    tried and a miss costs the weight rather than the render."""
    path = find(family, weight) or find(FALLBACK, weight)
    if path is None:
        log.warning("no font file for %r and none for %r either — drawing the chat in "
                    "Pillow's built-in face. Point $%s at a folder with a .ttf in it",
                    family, FALLBACK, typeface.ENV_DIRS)
        return ImageFont.load_default(size)
    try:
        font = ImageFont.truetype(str(path), size)
    except OSError as e:
        log.warning("%s will not load at %dpx (%s)", path, size, e)
        return ImageFont.load_default(size)
    if weight != "regular":
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
def _emoji() -> tuple[ImageFont.FreeTypeFont, int] | None:
    """The colour emoji face and the size it loaded at, or None where there is none.

    None is a usable answer: the caller draws the emoji with the text face instead and
    gets the black-and-white glyph, which is wrong-looking but not missing."""
    path = find("Noto Color Emoji")
    if path is None or not typeface.is_colour_emoji(path):
        return None
    for px in (EMOJI_PX, *EMOJI_FALLBACK_PX):
        try:
            return ImageFont.truetype(str(path), px), px
        except OSError:
            continue  # a bitmap font being asked for a strike it does not carry
    log.warning("the colour emoji font at %s loads at no size we tried", path)
    return None


def emoji_font() -> ImageFont.FreeTypeFont | None:
    """The colour emoji face, or None where this machine has none."""
    got = _emoji()
    return got[0] if got else None


def emoji_px() -> int:
    """The size :func:`emoji_font` is loaded at — the size it must be DRAWN at.

    A bitmap emoji font renders at its strike and nothing else, so the scratch canvas
    has to be sized from the font rather than from a constant."""
    got = _emoji()
    return got[1] if got else EMOJI_PX
