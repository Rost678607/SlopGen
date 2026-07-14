"""Stock footage providers: Pexels, Pixabay and a local assets/footage folder.

Downloads are cached in state/cache/footage keyed by URL hash, so repeated
keywords across runs don't redownload.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import httpx

if TYPE_CHECKING:
    from .generate import GenParams

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# How many top-scoring candidates to weighted-shuffle for variety (see _ranked).
RANK_TOPK = 5


class FootageError(Exception):
    pass


def _tok(s: str) -> set[str]:
    """Content tokens (len > 2) for lexical overlap scoring; stock APIs are
    English-indexed so ASCII word splitting is enough."""
    return {t for t in re.split(r"[^0-9a-z]+", s.lower()) if len(t) > 2}


def _ranked(query: str, items: list, meta: "Callable[[dict], str]") -> list:
    """Order stock candidates by how well their own metadata matches `query`
    instead of taking a random one — that random pick is why results land
    "off-topic". `meta(item)` returns the item's searchable text (tags / alt /
    url slug). Provider order breaks ties (APIs already sort by relevance).

    To keep repeated runs from always yielding the identical top clip, the top
    RANK_TOPK matches are weighted-shuffled (weight = score + 1) rather than
    taken strictly in order; lower-scoring items follow as ranked fallback.
    """
    qt = _tok(query)
    scored = sorted(
        ((len(qt & _tok(meta(it))), -i, it) for i, it in enumerate(items)),
        key=lambda t: (t[0], t[1]),
        reverse=True,
    )
    pool = [(score + 1, it) for score, _, it in scored[:RANK_TOPK]]
    tail = [it for _, _, it in scored[RANK_TOPK:]]
    out: list = []
    while pool:  # weighted sampling without replacement over the top-K
        total = sum(w for w, _ in pool)
        r = random.uniform(0, total)
        acc = 0.0
        for idx, (w, it) in enumerate(pool):
            acc += w
            if r <= acc:
                out.append(pool.pop(idx)[1])
                break
    return out + tail


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".mp4")


def _download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(65536):
                f.write(chunk)
        tmp.rename(dest)
    return dest


def _pexels(query: str, cache_dir: Path, exclude: set[str]) -> Path | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    r = httpx.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "orientation": "portrait", "per_page": 20},
        headers={"Authorization": key},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    videos = r.json().get("videos", [])
    # Pexels videos carry no tags; the page-url slug holds the descriptive words.
    videos = _ranked(query, videos, lambda v: v.get("url", ""))
    for v in videos:
        # prefer files close to 1080 wide, portrait
        files = sorted(
            (f for f in v.get("video_files", []) if f.get("width") and f["width"] <= 1440),
            key=lambda f: -f["width"],
        )
        for f in files:
            url = f["link"]
            if url in exclude:
                continue
            exclude.add(url)
            return _download(url, _cache_path(cache_dir, url))
    return None


def _pixabay(query: str, cache_dir: Path, exclude: set[str]) -> Path | None:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    r = httpx.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    hits = r.json().get("hits", [])
    hits = _ranked(query, hits, lambda h: h.get("tags", ""))
    for h in hits:
        for size in ("large", "medium"):
            f = h.get("videos", {}).get(size)
            if f and f.get("url") and f["url"] not in exclude:
                exclude.add(f["url"])
                return _download(f["url"], _cache_path(cache_dir, f["url"]))
    return None


def _local(footage_dir: Path, exclude: set[str], exts: set[str] = VIDEO_EXTS) -> Path | None:
    if not footage_dir.is_dir():
        return None
    files = [p for p in footage_dir.iterdir() if p.suffix.lower() in exts and str(p) not in exclude]
    if not files:  # all used already — allow repeats rather than fail
        files = [p for p in footage_dir.iterdir() if p.suffix.lower() in exts]
    if not files:
        return None
    pick = random.choice(files)
    exclude.add(str(pick))
    return pick


def _pexels_photo(query: str, cache_dir: Path, exclude: set[str]) -> Path | None:
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    r = httpx.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "orientation": "portrait", "per_page": 20},
        headers={"Authorization": key},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    photos = r.json().get("photos", [])
    photos = _ranked(query, photos, lambda p: p.get("alt", "") or "")
    for p in photos:
        url = p.get("src", {}).get("large2x") or p.get("src", {}).get("large")
        if url and url not in exclude:
            exclude.add(url)
            dest = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
            return _download(url, dest)
    return None


def _pixabay_photo(query: str, cache_dir: Path, exclude: set[str]) -> Path | None:
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    r = httpx.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "per_page": 20, "safesearch": "true", "orientation": "vertical"},
        timeout=30,
    )
    if r.status_code != 200:
        return None
    hits = r.json().get("hits", [])
    hits = _ranked(query, hits, lambda h: h.get("tags", ""))
    for h in hits:
        url = h.get("largeImageURL") or h.get("webformatURL")
        if url and url not in exclude:
            exclude.add(url)
            dest = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
            return _download(url, dest)
    return None


def find_image(
    query: str,
    fallback_keywords: list[str],
    providers: list[str],
    cache_dir: Path,
    images_dir: Path,
    exclude: set[str],
    gen: "GenParams | None" = None,
) -> Path:
    """Narration-synced photo lookup; same provider chain semantics as find_clip.
    `pollinations` generates an image from the query instead of searching stock."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    queries = [query] + fallback_keywords
    for provider in providers:
        for q in queries:
            if not q.strip():
                continue
            try:
                if provider == "pexels":
                    img = _pexels_photo(q, cache_dir, exclude)
                elif provider == "pixabay":
                    img = _pixabay_photo(q, cache_dir, exclude)
                elif provider == "pollinations":
                    from .generate import GenParams, pollinations_image
                    img = pollinations_image(q, cache_dir, exclude, gen or GenParams())
                elif provider == "local":
                    img = _local(images_dir, exclude, IMAGE_EXTS)
                else:
                    img = None
            except httpx.HTTPError:
                img = None
            if img:
                return img
    raise FootageError(
        f"no image found for '{query}' (providers: {providers}; "
        "set PEXELS_API_KEY/PIXABAY_API_KEY, add 'pollinations' (free, no key), "
        "or drop images into assets/images)"
    )


def find_clip(
    keywords: list[str],
    fallback_keywords: list[str],
    providers: list[str],
    cache_dir: Path,
    footage_dir: Path,
    exclude: set[str],
    gen: "GenParams | None" = None,
) -> Path:
    """Try each keyword against each provider in order; raise if nothing found.
    `wan` generates a clip from the query (HF Spaces) instead of searching stock."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    queries = [" ".join(keywords)] + keywords + fallback_keywords
    for provider in providers:
        for q in queries:
            if not q.strip():
                continue
            try:
                if provider == "pexels":
                    clip = _pexels(q, cache_dir, exclude)
                elif provider == "pixabay":
                    clip = _pixabay(q, cache_dir, exclude)
                elif provider == "wan":
                    from .generate import GenParams, wan_video
                    clip = wan_video(q, cache_dir, exclude, gen or GenParams())
                elif provider == "local":
                    clip = _local(footage_dir, exclude)
                else:
                    clip = None
            except httpx.HTTPError:
                clip = None
            if clip:
                return clip
    raise FootageError(
        f"no footage found for {keywords} (providers: {providers}; "
        "set PEXELS_API_KEY/PIXABAY_API_KEY, add 'wan' (free HF Spaces, slow), "
        "or drop clips into assets/footage)"
    )
