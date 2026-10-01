"""Reading reddit, without a library and — where reddit still allows it — without a key.

Reddit serves every page it has as JSON by putting `.json` on the end of the URL, and
that used to be the whole client. It is still the first thing tried, because on the
networks where it works it needs nothing at all. Measured from this machine in October
2026, it no longer does: `www.reddit.com/…/hot.json` answers 403 with an HTML page and
`old.reddit.com` redirects to a login wall, whatever `User-Agent` is sent. That is
reddit's own posture and not something a client can be clever about.

So there is a second road, and it is the documented one: register a script app at
https://www.reddit.com/prefs/apps, put its id and secret in `.env` as
`REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`, and this asks reddit for an app token
and reads through `oauth.reddit.com` — free, and a hundred requests a minute. The
keyless attempt stays in front of it rather than being deleted, because the block is
per-network: the server this deploys to may well be on one where it still answers, and
a client that demanded a key everywhere would be demanding one for nothing.

Either way an honest `User-Agent` is sent. The default one every HTTP library uses is
rate-limited into uselessness within a few requests, and the documented way to be
treated as a person rather than as a swarm is to say who you are — so :data:`AGENT`
names this program and its repository, which is exactly what reddit's rules ask for.

Two things are offered because they are two different acts. :func:`listing` is
browsing — what is on a subreddit right now, as titles with their scores — and brings
back no comments at all, because the point of browsing is to decide what is worth
reading. :func:`thread` is reading one, and brings back the post and its comment tree.
A video is made of the second; the first is how you find which.

The tree is kept as a tree the whole way through (:mod:`.exports` reads the same
shape out of a saved file), because reddit's unit is not a chat line — it is a post
with replies to replies, and the skin draws that as the indent it already is.
"""

from __future__ import annotations

import logging
import os
import re
import time

import httpx

from .exports import Piece, reddit_json

log = logging.getLogger(__name__)

BASE = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
ID_VAR, SECRET_VAR = "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"
# What reddit's own rules ask a keyless client to send: a real name and a way to find
# whoever is behind it. A library's default agent is throttled almost immediately.
AGENT = "slopgen/1.0 (+https://github.com/Rost678607/SlopGen)"
TIMEOUT = 20.0

# How the operator may name a subreddit: bare, `r/name`, or the whole URL of one.
_SUB = re.compile(r"(?:^|reddit\.com/)r/([A-Za-z0-9_]{2,40})", re.I)
# …and how they may name a thread: any reddit URL with a `comments/<id>` in it, or the
# bare id, which is what the site itself calls a post.
_POST = re.compile(r"comments/([a-z0-9]{4,12})", re.I)

SORTS = ("hot", "top", "new", "rising")


class RedditError(Exception):
    """Something reddit would not give us, said in a sentence worth showing the
    operator rather than a status code."""


# The app token, and when it stops being one. Held in the module because it is good
# for an hour and asking for a fresh one per request would spend half the rate limit
# on asking.
_TOKEN: tuple[str, float] | None = None


def credentials() -> tuple[str, str]:
    """The script app's id and secret, or a pair of empty strings."""
    return os.environ.get(ID_VAR, "").strip(), os.environ.get(SECRET_VAR, "").strip()


def _token() -> str | None:
    """An app-only token, asked for at most once an hour. None without credentials."""
    global _TOKEN
    cid, secret = credentials()
    if not (cid and secret):
        return None
    if _TOKEN and _TOKEN[1] > time.time() + 30:
        return _TOKEN[0]
    try:
        r = httpx.post(f"{BASE}/api/v1/access_token", timeout=TIMEOUT,
                       auth=(cid, secret), headers={"User-Agent": AGENT},
                       data={"grant_type": "client_credentials"})
    except httpx.HTTPError as e:
        raise RedditError(f"reddit would not hand out a token ({type(e).__name__})")
    if r.status_code == 401:
        raise RedditError(f"reddit refused those credentials — check {ID_VAR} and "
                          f"{SECRET_VAR}, and that the app is a 'script' app")
    if r.status_code >= 400:
        raise RedditError(f"reddit answered {r.status_code} asking for a token")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    token = str(body.get("access_token") or "")
    if not token:
        raise RedditError("reddit handed back no token")
    _TOKEN = (token, time.time() + float(body.get("expires_in") or 3600))
    return token


def _fetch(host: str, path: str, params: dict, token: str | None):
    headers = {"User-Agent": AGENT}
    if token:
        headers["Authorization"] = f"bearer {token}"
    try:
        return httpx.get(f"{host}{path}", params=params, timeout=TIMEOUT,
                         headers=headers, follow_redirects=False)
    except httpx.HTTPError as e:
        raise RedditError(f"reddit is not answering ({type(e).__name__})")


def _get(path: str, params: dict | None = None):
    """One read, through whichever road is open.

    Keyless first and the app second, because the block is per-network: demanding a
    key on a machine where the public road works would be demanding one for nothing.
    A 403 or a redirect to the login wall is not an error worth showing yet — it is
    the signal to try the other road, and only when there is no other road does it
    become the message that says how to open one."""
    params = dict(params or {})
    token = _token()
    if token is None:
        r = _fetch(BASE, path, params, None)
        if r.status_code < 400 and not r.is_redirect:
            return _json(r)
        if r.status_code in (401, 403) or r.is_redirect:
            raise RedditError(
                "reddit will not answer this machine without an app: register a "
                f"script app at {BASE}/prefs/apps and put its id and secret in .env "
                f"as {ID_VAR} and {SECRET_VAR}")
        _raise(r)
    r = _fetch(OAUTH, path, params, token)
    if r.status_code < 400:
        return _json(r)
    _raise(r)


def _raise(r) -> None:
    if r.status_code == 429:
        raise RedditError("reddit is rate-limiting this app — wait a minute")
    if r.status_code == 403:
        raise RedditError("reddit refused: the subreddit may be private or quarantined")
    if r.status_code == 404:
        raise RedditError("there is nothing at that address")
    raise RedditError(f"reddit answered {r.status_code}")


def _json(r):
    try:
        return r.json()
    except ValueError:
        raise RedditError("reddit answered with something that is not JSON — it may "
                          "have served a login wall instead")


def subreddit(text: str) -> str:
    """The subreddit an operator meant, out of whatever they typed."""
    hit = _SUB.search(text or "")
    if hit:
        return hit.group(1)
    bare = (text or "").strip().strip("/")
    if re.fullmatch(r"[A-Za-z0-9_]{2,40}", bare):
        return bare
    raise RedditError(f"{text!r} does not name a subreddit")


def post_id(text: str) -> str:
    """The post an operator meant, out of a URL or a bare id."""
    hit = _POST.search(text or "")
    if hit:
        return hit.group(1)
    bare = (text or "").strip().strip("/")
    if re.fullmatch(r"[a-z0-9]{4,12}", bare, re.I):
        return bare
    raise RedditError(f"{text!r} does not name a thread")


def listing(where: str, sort: str = "hot", limit: int = 25,
            period: str = "day") -> list[dict]:
    """What is on a subreddit, as something to choose from.

    No comments: browsing is deciding what is worth reading, and a hundred threads
    with their comment trees attached is several megabytes to answer a question about
    titles. `period` only means anything to `top`, which is the sort that has one."""
    name = subreddit(where)
    sort = sort if sort in SORTS else "hot"
    params = {"limit": max(1, min(int(limit), 100)), "raw_json": 1}
    if sort == "top":
        params["t"] = period
    data = _get(f"/r/{name}/{sort}.json", params)
    out = []
    for child in ((data or {}).get("data") or {}).get("children") or []:
        d = (child or {}).get("data") or {}
        if child.get("kind") != "t3" or d.get("stickied"):
            continue
        out.append({
            "id": str(d.get("id") or ""),
            "title": str(d.get("title") or ""),
            "author": f"u/{d.get('author') or 'somebody'}",
            "score": int(d.get("score") or 0),
            "comments": int(d.get("num_comments") or 0),
            # a self-post is a story somebody wrote; a link post is a picture with
            # arguing under it, and only one of those reads aloud
            "text": str(d.get("selftext") or "")[:400],
            "over_18": bool(d.get("over_18")),
            "url": f"{BASE}{d.get('permalink') or ''}",
        })
    log.info("reddit: r/%s %s — %d threads", name, sort, len(out))
    return out


def thread(where: str, limit: int = 120, depth: int = 6) -> list[Piece]:
    """One thread: the post, and its comments as the tree they are.

    Parsed by the very function that reads a saved `.json` off disk
    (`exports.reddit_json`), because they are the same bytes — the export IS this
    request, saved. One reader means a thread that imports correctly also fetches
    correctly, and a fix to either is a fix to both."""
    pid = post_id(where)
    data = _get(f"/comments/{pid}.json",
                {"limit": max(1, min(int(limit), 500)),
                 "depth": max(1, min(int(depth), 10)), "raw_json": 1})
    pieces = reddit_json(data)
    for p in pieces:
        p.source = f"{BASE}/comments/{pid}"
    if not pieces or not pieces[0].lines:
        raise RedditError("that thread has nothing readable in it")
    return pieces
