"""Compiling the operator's description of the LOOK into prompt tags.

The operator says how the video should look in their own words — one of them
(«аниме») or three paragraphs about grainy 16mm and sodium street light — and that
sentence has to end up on EVERY generated prompt, in the only dialect image and video
models actually respond to: short comma-separated English tags.

Neither end of that range survives being pasted in raw. A bare «аниме» reaches the
generator as one foreign token it renders as on-screen text, and even written as
"anime" it is a word, not a look: what pins the look is the handful of tags the word
stands for (cel shading, flat colour, thick clean linework, large expressive eyes,
painted backgrounds). A long description is the opposite failure — it is mostly
sentences, and it says things about the story as well as the picture, so appended to
forty shot prompts it out-weighs the shots themselves. So both go through one compile
pass: expand the short one into what it means, boil the long one down to what it
shows, and emit the same shape either way.

The result is cached on disk under `state/cache/styles/`, keyed by the text, so the
compile costs one call the first time a look is used and nothing on every resume,
every episode and every later run that reuses it.

What comes back is STYLE ONLY, and that is the contract this module defends: it is
glued onto prompts that already carry a subject, so a single stray noun ("a girl in
a school uniform") would put that noun into all forty shots.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# How many tags a compiled look may carry. It rides on every prompt next to the
# shot's own words, and past this it stops describing the picture and starts BEING
# the picture — the same crowding the drama's appearance budget guards against
# (see pipeline/stages/drama_footage.CROWD_TAG_BUDGET).
MAX_TAGS = 16
# Ceiling on the operator's own words when the compile could not run (see _fallback).
# Uncompiled text has no tags to count, so it is capped by length instead — a whole
# paragraph appended to every prompt would bury the shot it is riding on.
FALLBACK_CHARS = 220

_LATIN = re.compile(r"[A-Za-z]")


SYSTEM = (
    "You are a prompt engineer for AI image/video generation. The operator has "
    "described how their whole video should LOOK. Turn that description into ONE "
    "English, comma-separated tag descriptor that will be appended to every single "
    "shot prompt of the video.\n"
    "Rules:\n"
    "  • STYLE ONLY. The shot prompts already say what is in the picture; yours says "
    "how it is drawn. Never emit a subject, character, place, action, event or story "
    "detail — if the description mentions any, keep only what they imply about the "
    "look and drop the rest.\n"
    "  • A one-word description ('anime', 'noir', 'claymation') is a whole tradition "
    "of images, and the word alone does not pin it. Name what it actually is made of: "
    "medium and technique, linework, shading, colour palette, lighting, texture and "
    "grain, level of detail, and the camera/lens/film character where the look has "
    "one. A long description is the opposite job: keep every concrete visual fact and "
    "throw away the sentences around them.\n"
    f"  • Between 5 and {MAX_TAGS} tags, the most defining first. Each tag is a few "
    "words at most. No sentences, no explanations.\n"
    "  • ENGLISH ONLY, whatever language the description was written in — a non-Latin "
    "word reaches the generator as literal text printed across the frame.\n"
    "  • No negatives ('no text', 'without people'): they are not honoured by these "
    "models as written and would only spend the budget. No weights, no parentheses, "
    "no brackets, no resolution or aspect-ratio tags, no living artists' names — name "
    "the movement, era, studio tradition or technique instead.\n"
    'Respond with JSON only: {"style": "tag, tag, tag"}.'
)

# What the picture is made of, when the run has already settled it. A look compiled
# for a slideshow must not ask for camera moves the still cannot make, and one for
# clips may say how the motion itself looks.
MEDIUM = {
    "photo": (
        "\nThis video is a slideshow of STILL images. Emit no motion, camera-movement "
        "or frame-rate tags; describe the still image alone.\n"
    ),
    "video": (
        "\nThis video is made of moving clips. Tags about motion, camera movement or "
        "frame-rate character are welcome where the described look has them.\n"
    ),
}


def _tags(text: str) -> list[str]:
    """Split a compiled descriptor into clean tags, dropping empties and repeats."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[,\n]", text):
        tag = " ".join(raw.split()).strip(" .;")
        if not tag or tag.lower() in seen:
            continue
        seen.add(tag.lower())
        out.append(tag)
    return out


def _english(text: str) -> str:
    """Drop every word carrying a non-Latin letter. Generators print such words into
    the frame as captions (see drama_footage._drop_foreign), and a model that ignored
    the English-only instruction must not be able to burn Cyrillic across all forty
    shots."""
    kept = [w for w in text.split() if not any(c.isalpha() and not c.isascii() for c in w)]
    return " ".join(kept)


def _clean(text: str) -> str:
    tags = [t for t in (_english(t) for t in _tags(text)) if t.strip()]
    return ", ".join(tags[:MAX_TAGS])


def _fallback(text: str) -> str:
    """What to use when the compile could not run. The operator's own words, cleaned
    the same way — worth keeping when they wrote in English ("grainy 16mm film"),
    worth nothing when they did not, and an empty look is the correct answer there:
    a shot with no style tags is still a shot, while one captioned in Cyrillic is
    ruined."""
    out = _clean(text)
    if not _LATIN.search(out):
        return ""
    if len(out) <= FALLBACK_CHARS:
        return out
    cut = out[:FALLBACK_CHARS]
    return cut.rsplit(",", 1)[0] if "," in cut else cut.rsplit(" ", 1)[0]


def _cached(cache_dir: Path | None, key: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".txt")


def compile_style(llm, text: str, cache_dir: Path | None = None, medium: str = "") -> str:
    """Compile a free-form description of the look into prompt tags.

    Returns "" for an empty description. A failed call never takes a run down: the
    operator's own words come back instead when they can be used at all (see
    :func:`_fallback`), because a picture in the wrong style still beats no picture."""
    text = (text or "").strip()
    if not text:
        return ""

    path = _cached(cache_dir, f"{medium}\x00{text}")
    if path is not None and path.exists():
        return path.read_text(encoding="utf-8").strip()

    system = SYSTEM + MEDIUM.get(medium, "")
    try:
        data = llm.complete_json("style_compile", system, f"Look to compile:\n{text}")
        raw = data.get("style", "")
        # a model asked for a string sometimes answers with the list of tags instead
        style = _clean(", ".join(str(t) for t in raw) if isinstance(raw, list) else str(raw))
    except Exception as e:  # noqa: BLE001 — any failure degrades to the raw words
        log.warning("could not compile the visual style (%s) — using it as written", e)
        style = ""
    style = style or _fallback(text)

    if path is not None and style:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(style, encoding="utf-8")
    return style
