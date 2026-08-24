"""Turning a shot into a SEARCH task for the operator.

User-assisted search is the third way material reaches a video, next to fetching it
from a stock API and generating it. slopgen cannot search the way a person can — it
has no eye for whether a clip is usable — so it does the half it is good at: deciding
what the shot needs and writing the words most likely to surface it.

That is genuinely a translation job, not a copy. What a shot prompt says and what a
stock site indexes are different languages: a drama's beat reads "Марта bends over the
sorting table, lamplight catching the wax seals, slow push-in", and no stock library
has that. It has "woman sorting mail warm light" and "hands wax seal close up". So the
model rewrites each shot twice over — once as a plain instruction for the person doing
the looking (in their language), once as several short English queries for the sites
(which index English almost exclusively, whatever the narration is in).

It also decides between a still and a clip per shot, because that choice belongs to
the material rather than to a setting: a wax seal in close-up wants a photograph, mules
on a path want motion, and a person told "find video" for the former comes back with
nothing usable. It is advice, not a rule — the manifest records whatever the operator
actually delivers (see `pipeline/manual.attach`).
"""

from __future__ import annotations

from dataclasses import dataclass

# Long runs have a hundred-odd shots; asking for all of them in one response gets the
# same front-loaded attention the script stage windows around (see stages/beats), and
# the tail comes back as "similar to the above".
BATCH = 20

SYSTEM = (
    "You brief a person who is about to go and FIND existing footage and photographs "
    "on stock sites (Pexels, Pixabay, Unsplash, and the like) for a vertical short "
    "video. For each shot you are given, you produce the search task.\n"
    "For each shot give:\n"
    '  • "want": "photo" or "video" — which suits THIS shot. A held detail, a texture, '
    "a face, an object, a document wants a photo; movement, a process, weather, a "
    "crowd, anything the eye should watch happen wants video. Short shots lean photo, "
    "long ones lean video, but the content decides.\n"
    '  • "brief": ONE sentence in {lang} telling the searcher what to look for and '
    "what matters about it — framing, mood, what must NOT be in it (\"no faces\", \"no "
    "text\", \"shot from above\"). This is read by a person, not typed into a box.\n"
    '  • "queries": 3-5 SHORT ENGLISH search queries, most promising first. Stock '
    "libraries index English and match on plain nouns: two to four common words each, "
    "no punctuation, no camera jargon, no proper nouns, no invented terms. Vary them — "
    "a literal one, a broader one, a mood one — so that if the first returns nothing "
    "the next still might. Never translate the shot word for word: describe the kind "
    "of picture a stock library would actually hold.\n"
    "The shots belong to one video and are given in order; keep them visually varied "
    "rather than sending the searcher after the same clip five times.\n"
    'Respond with JSON only: {{"shots": [{{"id": "<the id given>", "want": "photo|video", '
    '"brief": "...", "queries": ["...", "..."]}}, ...]}} — one entry per shot, in the '
    "same order, with the ids exactly as given."
)


@dataclass
class SearchTask:
    want: str  # "photo" | "video"
    brief: str
    queries: list[str]


def _parse(data: dict, ids: list[str], medium: str = "") -> dict[str, SearchTask]:
    out: dict[str, SearchTask] = {}
    rows = data.get("shots")
    if not isinstance(rows, list):
        return out
    known = set(ids)
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id", "")).strip()
        if sid not in known:
            continue
        # a pinned medium is the operator's decision, not the model's to revisit —
        # telling it in the prompt is not enough, since it answers "video" out of habit
        want = medium or str(row.get("want", "")).strip().lower()
        queries = [
            " ".join(str(q).split())
            for q in (row.get("queries") or [])
            if str(q).strip()
        ]
        out[sid] = SearchTask(
            want=want if want in ("photo", "video") else "",
            brief=str(row.get("brief", "")).strip(),
            queries=queries[:5],
        )
    return out


# When the operator has already said what the picture is made of, the per-shot choice
# is not the model's to make: they asked for a slideshow, so every task is a photograph.
FORCED = (
    "\nThis video is made of {kind}s ONLY. Every \"want\" is \"{kind}\" — do not choose "
    "otherwise — and every brief and query must be for {kind} material.\n"
)


def search_tasks(
    llm, shots: list[tuple[str, str, float]], lang: str = "English",
    on_progress=None, medium: str = "",
) -> dict[str, SearchTask]:
    """Brief the searcher on every shot: {shot_id: SearchTask}.

    `shots` is (id, what the shot shows, seconds). `medium` pins photo-or-video when the
    operator has already decided; empty leaves the choice per shot. A shot the model
    skips or mangles simply gets no entry, and the caller falls back to what it already
    had — a missing brief costs the operator a worse search, never the run."""
    out: dict[str, SearchTask] = {}
    batches = [shots[i:i + BATCH] for i in range(0, len(shots), BATCH)]
    for n, batch in enumerate(batches):
        listing = "\n".join(
            f"{sid} · {secs:.1f}s · {desc}" for sid, desc, secs in batch
        )
        try:
            system = SYSTEM.format(lang=lang)
            if medium in ("photo", "video"):
                system += FORCED.format(kind=medium)
            data = llm.complete_json(
                "search_tasks", system, f"Shots to brief:\n{listing}",
            )
        except Exception:
            continue  # this batch keeps its fallback; the rest still get briefed
        out.update(_parse(data, [sid for sid, _, _ in batch], medium))
        if on_progress:
            on_progress("search", n + 1, len(batches))
    return out
