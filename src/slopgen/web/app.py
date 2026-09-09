"""The browser UI: a second frontend, not a rewrite.

The core never knew about the terminal one either — `pipeline/`, `config/`, `media/`
and the rest hold no reference to `tui/` — so this is a new consumer of the same two
plain-data callbacks and the same on-disk state, and nothing in the pipeline had to
move to make room for it. What it adds is the half a terminal genuinely cannot do:
pointing at a picture. Marking the crop regions of a frame card is a pointing job, and
every way of faking that in a text grid is a worse version of a mouse.

Auth is deliberately blunt. No password means loopback only, which needs no thinking
about; a password is what unlocks binding to the network, because the same server can
start runs and spend real API quota. The cookie is a random token held in memory, so
restarting the server logs everyone out — which for a tool one person runs on their
own machine is the correct amount of session management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)
from starlette.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..config import ConfigStore, RunParams
from ..config.loader import (delete_config, fandom_docs, file_sha, frames_dir,
                             lore_sha, read_lore, update_global, write_character,
                             write_config, write_frame_card)
from ..config.envfile import set_env_var
from ..llm import characters as char_ai
from ..llm import lore as lore_ai
from ..llm import rewrite as bp_ai
from ..llm import topic as topic_ai
from ..llm.client import ChatLLM, MODEL_PRESETS, PROVIDERS
from .. import labels
from ..media.generate import (PHOTO_MODELS, VIDEO_MODELS, env_keys,
                              model_clip_seconds)
from ..tts import ENGINES as TTS_ENGINES
from ..tts import refs
from ..tts.base import VOICE_PRESETS
from ..tts.demo import DEMO_TEXT, speak as speak_demo
from ..config.models import (AccountConfig, AdConfig, CharacterConfig, CropTarget,
                             FrameCard, LLMProfile, OrchestrationConfig,
                             OrchestrationConfig, OrchestrationStage, PresetConfig,
                             Rect, VisualsConfig,
                             VoiceConfig)
from ..media.stock import IMAGE_EXTS, VIDEO_EXTS
from ..pipeline import manual, review
from ..pipeline.loop import check_params
from ..pipeline.checkpoint import Checkpoint
from ..models import CATALOG as MODEL_CATALOG
from ..models import ModelStore, human_size
from .params import (FILTER_HELP, drama_params, fandom_params, info_params,
                     loop_of, override_fields)
from .runs import Supervisor, parked

if TYPE_CHECKING:  # the bot imports this module, never the other way round
    from ..bot.auth import TelegramAuth

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

ALLOWED_SUFFIXES = IMAGE_EXTS | VIDEO_EXTS


def create_app(store: ConfigStore, bound: str = "", bound_port: int = 0,
               tg: "TelegramAuth | None" = None) -> FastAPI:
    """The app. `tg`, when given, is the bot serving this page as its Mini App.

    It does two things and no more: it lets a Telegram user sign in with what Telegram
    already signed for them (`/api/tg-login`), and — because a bot that has a Mini App
    also has a public address — it closes the anonymous door. Without a password the
    server is loopback-only and open, which is right at a desk and would be catastrophic
    at the far end of a tunnel."""
    cfg = store.global_cfg.web
    app = FastAPI(title="slopgen", docs_url=None, redoc_url=None)
    sup = Supervisor(store, cfg.max_parallel)
    sessions: set[str] = set()

    @app.api_route("/static/{name}", methods=["GET", "HEAD"])
    async def static_file(name: str) -> FileResponse:
        """Served by hand rather than mounted, for one reason: `no-store`.

        A cached asset is the right default for a site and the wrong one for a tool
        that is edited and reloaded all day — a stale script that still calls a dialog
        the source no longer has is a very confusing half hour. Nothing here is big
        enough for caching to be worth that."""
        path = (HERE / "static" / name).resolve()
        if not path.is_file() or (HERE / "static").resolve() not in path.parents:
            raise HTTPException(status_code=404, detail="no such file")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.on_event("startup")
    async def _bind() -> None:
        sup.bind(asyncio.get_running_loop())
        found = sup.adopt_all(Path(store.global_cfg.paths.output))
        if found:
            log.info("web: %d runs found on disk", found)

    @app.on_event("shutdown")
    async def _stop() -> None:
        sup.shutdown()

    # -- auth --------------------------------------------------------------

    @app.middleware("http")
    async def carry_token(request: Request, call_next):
        """Let the session arrive as a header or a `?t=` as well as a cookie.

        Inside Telegram the page can be a third-party iframe (Telegram Web) where the
        browser is entitled to drop our cookie on the floor, and a Mini App that works
        on a phone and not on a laptop is a bug reported as "it just spins". So the
        token has two other ways in — and the query one exists because `<img src>`,
        `<video src>` and `EventSource` cannot send a header at all.

        Rewriting the request's cookie header rather than teaching forty routes about
        this is the whole trick: everything downstream keeps reading one cookie."""
        token = request.query_params.get("t") or request.headers.get("x-slopgen-token")
        if token and "slopgen" not in request.cookies:
            jar = request.headers.get("cookie", "")
            merged = f"{jar}; slopgen={token}" if jar else f"slopgen={token}"
            request.scope["headers"] = [(k, v) for k, v in request.scope["headers"]
                                        if k != b"cookie"] + [(b"cookie", merged.encode())]
        return await call_next(request)

    def guard(token: str | None) -> None:
        if token in sessions:
            return
        # No password used to mean no door at all, which is safe on loopback and only
        # there. A bot serving this page has an address on the open internet, so its
        # presence is itself a reason to ask who is knocking.
        if cfg.password or tg is not None:
            raise HTTPException(status_code=401, detail="not signed in")

    def _issue(request: Request, payload: dict) -> JSONResponse:
        """Hand out a session, as a cookie and as a token in the body.

        Both, because they fail in different places: the cookie carries `<video src>`
        and the event stream, and the token survives a browser that will not keep a
        third-party cookie. `SameSite=None` needs `Secure`, and `Secure` needs the
        request to have actually arrived over TLS — which behind the tunnel it did,
        one hop upstream, hence the forwarded header."""
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        https = (request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
        r = JSONResponse({**payload, "token": token})
        r.set_cookie("slopgen", token, httponly=True,
                     samesite="none" if https else "lax", secure=https)
        return r

    @app.post("/api/login")
    async def login(request: Request, password: str = Form(...)) -> JSONResponse:
        if not cfg.password or not secrets.compare_digest(password, cfg.password):
            raise HTTPException(status_code=401, detail="wrong password")
        return _issue(request, {"ok": True})

    @app.post("/api/tg-login")
    async def tg_login(request: Request) -> JSONResponse:
        """Sign in with what Telegram already signed.

        `initData` is a query string the client hands the page, stamped with an HMAC
        only somebody holding the bot token can produce — so verifying it is the whole
        of authentication here, and there is no password to type on a phone. Being a
        valid Telegram user is not enough: the same allow-list the chat is filtered by
        decides this too, because the Mini App can start runs and spend real quota."""
        if tg is None:
            raise HTTPException(status_code=404, detail="this server has no bot")
        body = await request.json()
        who = tg.verify(str(body.get("init_data", "")))
        if who is None:
            raise HTTPException(status_code=401, detail="Telegram did not vouch for that")
        log.info("web: %s signed in through Telegram", who)
        return _issue(request, {"ok": True, "user": who})

    def _t(key: str) -> str:
        """A word the server itself has to produce — a run's default title, a queue
        note. Looked up per call rather than at import, so switching the interface
        language reaches these too."""
        return labels.t(key, store.global_cfg.ui.lang)

    @app.get("/api/options")
    async def options(slopgen: str | None = Cookie(default=None)) -> dict:
        """Everything the start form has to offer, straight out of `configs/`.

        The form is deliberately thin: it offers what exists rather than what could be
        typed, because every one of these is a folder somebody has to have filled in
        first, and a run naming a world that is not there fails three stages later for
        a reason nobody can see from the message."""
        guard(slopgen)
        lang = store.global_cfg.ui.lang
        out = {
            "labels": labels.table(lang),
            "ui_lang": lang,
            "worlds": sorted(store.fandoms),
            "voices": ["resident", "chronicler", "usher"],
            "fits": ["exact", "close", "loose", "any"],
            "breakpoints": {m: review.available(m) for m in ("info", "drama", "fandom")},
            "languages": ["ru", "en"],
            # Config entries the operator wrote themselves: the NAME is what goes on
            # the wire, but on its own it says nothing — `ai_broll` and `classic` are
            # only meaningful next to the line the operator wrote about them. The
            # terminal has always shown both; the browser was showing bare names.
            "content_types": _described(store.content_types, key="content"),
            "visuals": _described(store.visuals, key="visuals"),
            "orchestrations": sorted(store.orchestrations),
            "characters": sorted(store.characters),
            "ads": _described(store.ads, "url"),
            "accounts": sorted(store.accounts),
            "cloned_voices": sorted(store.voices),
            # what a fandom run's picture can be made of. `frames` is the world's own
            # base (see pipeline/framebase); the rest generate or are supplied per shot
            "photo_sources": list(PHOTO_MODELS) + ["manual", "search"],
            "video_sources": list(VIDEO_MODELS),
            "tts_engines": sorted(TTS_ENGINES),
            "subtitle_styles": ["word_pop", "phrases", "karaoke"],
            "ad_modes": ["overlay", "native", "both"],
            # the montage look: {effect: dose 0-100}, laid over the finished picture
            # rather than asked of any model, which is why every mode offers it
            "filters": [{"key": k, "note": v} for k, v in FILTER_HELP.items()],
        }
        # What one QUEUED video may be given of its own, per mode — the controls the
        # loop's queue draws for a single entry. Built off the lists above rather than
        # beside them, so a world or an ad contract is named once (see params.override_fields).
        out["overrides"] = {m: override_fields(out, m) for m in ("info", "drama", "fandom")}
        return out

    @app.put("/api/ui")
    async def set_ui(request: Request,
                     slopgen: str | None = Cookie(default=None)) -> dict:
        """The interface language, which both frontends read from the same place."""
        guard(slopgen)
        lang = str((await request.json()).get("lang", "ru"))
        if lang not in ("ru", "en"):
            raise HTTPException(status_code=422, detail=f"no interface language {lang!r}")
        update_global("ui", {"lang": lang})
        store.global_cfg.ui.lang = lang
        return {"lang": lang}

    @app.get("/api/config")
    async def config() -> dict:
        """What the editor has to know about the video to be honest about the crop.

        A picture is fitted to the video's aspect BEFORE anything is cropped out of it
        (`media/ffmpeg.photo_filter`: scale to cover, then centre-crop), and a card's
        regions are fractions of what survives that. So an editor that lets you mark
        the whole picture is lying whenever the picture is not already the video's
        shape — the edges you marked are the edges that get cut off."""
        v = store.global_cfg.video
        return {"video": {"width": v.width, "height": v.height, "fps": v.fps}}

    @app.get("/api/me")
    async def me(slopgen: str | None = Cookie(default=None)) -> dict:
        return {"needs_password": bool(cfg.password),
                # whether the page may sign itself in with Telegram's own signature,
                # which is the only way in when the bot is serving and no password is set
                "telegram": tg is not None,
                "signed_in": slopgen in sessions or not (cfg.password or tg)}

    @app.post("/api/reload")
    async def reload(slopgen: str | None = Cookie(default=None)) -> dict:
        """Re-read `configs/` from disk.

        The store is built once when the server starts, which is right for a run — a
        run must not have the world change under it halfway through — and wrong for a
        long-lived page: worlds get added, lore gets edited in an outside markdown
        editor, characters get written by the frozen TUI in another terminal. Rather
        than hand out a new store and leave the old one captured in half a dozen
        closures, this refills the one everybody already holds."""
        guard(slopgen)
        store.__init__()  # noqa: PLC2801 — deliberate in-place refill, see above
        found = sup.adopt_all(Path(store.global_cfg.paths.output))
        return {"worlds": len(store.fandoms), "visuals": len(store.visuals), "runs": found}

    # -- the frame base ----------------------------------------------------

    def world_or_404(name: str):
        w = store.fandoms.get(name)
        if w is None:
            raise HTTPException(status_code=404, detail=f"no world named {name!r}")
        return w

    def card_or_404(world, card_name: str) -> FrameCard:
        for c in world.frames:
            if c.name == card_name:
                return c
        raise HTTPException(status_code=404, detail=f"no card named {card_name!r}")

    @app.get("/api/worlds")
    async def worlds(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        guard(slopgen)
        return [
            {"name": n, "cards": len(w.frames), "cast": len(w.cast),
             "usable": sum(1 for c in w.frames if c.usable)}
            for n, w in sorted(store.fandoms.items())
        ]

    @app.get("/api/worlds/{name}/cards")
    async def cards(name: str, slopgen: str | None = Cookie(default=None)) -> list[dict]:
        guard(slopgen)
        w = world_or_404(name)
        return [_card_json(name, c) for c in w.frames]

    @app.get("/api/worlds/{name}/cards/{card}/file")
    async def card_file(name: str, card: str, slopgen: str | None = Cookie(default=None)):
        guard(slopgen)
        c = card_or_404(world_or_404(name), card)
        p = c.path
        if p is None or not p.is_file():
            raise HTTPException(status_code=404, detail="this card has no picture yet")
        return FileResponse(p)

    @app.put("/api/worlds/{name}/cards/{card}")
    async def save_card(name: str, card: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        c = card_or_404(w, card)
        body = await request.json()
        for field in ("description", "note", "prompt"):
            if field in body:
                setattr(c, field, str(body[field]))
        if "retired" in body:
            c.retired = bool(body["retired"])
        if "targets" in body:
            c.targets = [_target(t) for t in body["targets"]]
            # the regions were just drawn on THIS picture, so this is the moment its
            # checksum means something (see config.card_is_stale)
            if c.path and c.path.is_file():
                c.file_sha = file_sha(c.path)
        write_frame_card(c)
        return _card_json(name, c)

    @app.post("/api/worlds/{name}/cards")
    async def new_card(name: str, file: UploadFile, description: str = Form(""),
                       prompt: str = Form(""), note: str = Form(""),
                       slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=415,
                                detail=f"{suffix or 'that'} is neither a picture nor a clip")
        root = frames_dir(w)
        root.mkdir(parents=True, exist_ok=True)
        stem = _free_stem(description or Path(file.filename or "кадр").stem,
                          {c.name for c in w.frames})
        dest = root / f"{stem}{suffix}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        c = FrameCard(name=stem, file=dest.name, prompt=prompt, description=description,
                      note=note, file_sha=file_sha(dest), root=root)
        write_frame_card(c)
        # into the loaded world too: ConfigStore is built once, and a card only on
        # disk would be invisible until the process restarts
        w.frames.append(c)
        return _card_json(name, c)

    @app.delete("/api/worlds/{name}/cards/{card}")
    async def retire_card(name: str, card: str,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        c = card_or_404(w, card)
        # retired, never deleted: a card may be in the plan of a parked run, and its
        # picture is something somebody made
        c.retired = True
        write_frame_card(c)
        return _card_json(name, c)

    # -- the settings the terminal keeps under Configuration ---------------

    # Which pydantic model backs each config kind, and where its files live. A table
    # rather than a branch per kind: the endpoints below are identical for all of
    # them, because a config IS its model dump (see config.write_config).
    KINDS = {
        "llm": (LLMProfile, "llm"),
        "presets": (PresetConfig, "presets"),
        "ads": (AdConfig, "ads"),
        "accounts": (AccountConfig, "accounts"),
        "visuals": (VisualsConfig, "visuals"),
        "characters": (CharacterConfig, "characters"),
    }

    def _store_of(kind: str) -> dict:
        return {"llm": store.llm_profiles, "presets": store.presets, "ads": store.ads,
                "accounts": store.accounts, "visuals": store.visuals,
                "characters": store.characters}[kind]

    @app.get("/api/configs/{kind}")
    async def list_configs(kind: str, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if kind not in KINDS:
            raise HTTPException(status_code=404, detail=f"no config kind {kind!r}")
        model, _ = KINDS[kind]
        items = {n: c.model_dump(mode="json") for n, c in sorted(_store_of(kind).items())}
        extra: dict = {}
        if kind == "llm":
            # a profile means nothing without knowing which one is in use and whether
            # its key is actually set — that is what the operator is really asking
            extra = {"active": store.global_cfg.llm.profile,
                     "providers": {p: dict(v) for p, v in PROVIDERS.items()},
                     "presets": MODEL_PRESETS,
                     "keys": {p: bool(env_keys(v["key_env"])) for p, v in PROVIDERS.items()}}
        return {"kind": kind, "items": items, "schema": _fields(model), **extra}

    @app.put("/api/configs/{kind}/{name}")
    async def save_config(kind: str, name: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Create or replace one config. Validated through its own model first, so a
        form cannot write a file the loader will refuse to read back."""
        guard(slopgen)
        if kind not in KINDS:
            raise HTTPException(status_code=404, detail=f"no config kind {kind!r}")
        model, subdir = KINDS[kind]
        body = await request.json()
        body["name"] = name
        try:
            cfg = model.model_validate(body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        if kind == "characters":
            write_character(Path("configs/characters") / f"{name}.toml", cfg)
        else:
            write_config(subdir, name, cfg.model_dump(mode="json"))
        _store_of(kind)[name] = cfg
        return cfg.model_dump(mode="json")

    @app.delete("/api/configs/{kind}/{name}")
    async def remove_config(kind: str, name: str,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if kind not in KINDS:
            raise HTTPException(status_code=404, detail=f"no config kind {kind!r}")
        _, subdir = KINDS[kind]
        gone = delete_config(subdir, name)
        _store_of(kind).pop(name, None)
        return {"deleted": gone}

    @app.post("/api/configs/llm/active")
    async def set_active_llm(request: Request,
                             slopgen: str | None = Cookie(default=None)) -> dict:
        """Pick which LLM profile a run uses. Lives in `[llm] profile` of the global
        file, which is where every consumer already reads it from."""
        guard(slopgen)
        name = str((await request.json()).get("profile", ""))
        if name and name not in store.llm_profiles:
            raise HTTPException(status_code=404, detail=f"no profile named {name!r}")
        update_global("llm", {"profile": name})
        store.global_cfg.llm.profile = name
        return {"active": name}

    @app.get("/api/keys")
    async def list_keys(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        """Which API keys are set — never their values.

        A key that has been typed in is a fact worth showing; the key itself is not
        something to hand back out over HTTP, and showing it would only invite it into
        a screenshot. `.env` holds them, gitignored, exactly as the terminal does it."""
        guard(slopgen)
        return [{"var": v, "what": what, "set": bool(env_keys(v)), "count": len(env_keys(v))}
                for v, what in KEY_VARS]

    @app.put("/api/keys/{var}")
    async def set_key(var: str, request: Request,
                      slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if var not in {v for v, _ in KEY_VARS}:
            raise HTTPException(status_code=404, detail=f"{var} is not a key this uses")
        value = str((await request.json()).get("value", "")).strip()
        set_env_var(var, value)
        return {"var": var, "set": bool(value), "count": len(env_keys(var))}

    # --- how this server is reached ------------------------------------------

    LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

    def at_the_machine(request: Request) -> bool:
        """Is the caller sitting at the machine the server runs on?

        This setting decides whether the server answers the network at all, so it is
        editable only from the machine itself. Changing it over the very network it
        opens would mean a password, once guessed, is enough to widen the hole it came
        through."""
        client = request.client.host if request.client else ""
        return client in LOOPBACK

    @app.get("/api/web")
    async def web_settings(request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """The setting, what it actually bound to, and whether this caller may change it.

        Those first two are different facts and the screen shows both: asking for the
        network without a password silently stays on loopback, so a field that merely
        echoed what was typed would show a machine reachable from the network that is
        not. The password itself is never sent, in either direction."""
        guard(slopgen)
        w = store.global_cfg.web
        return {"host": w.host, "has_password": bool(w.password),
                "bound": bound or w.host, "port": bound_port or w.port,
                "local": at_the_machine(request)}

    @app.put("/api/web")
    async def set_web_settings(request: Request,
                               slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if not at_the_machine(request):
            raise HTTPException(status_code=403,
                                detail="this can only be changed at the machine itself")
        body = await request.json()
        values: dict = {"host": str(body.get("host", "")).strip() or "127.0.0.1"}
        # An empty box means "leave it", not "clear it": the form never shows the
        # current password, so it cannot tell those two apart, and submitting it after
        # editing only the address would silently drop the one setting that keeps the
        # network binding shut. Clearing is therefore its own explicit flag.
        if body.get("password"):
            values["password"] = str(body["password"])
        elif body.get("clear_password"):
            values["password"] = ""
        update_global("web", values)
        for k, v in values.items():
            setattr(store.global_cfg.web, k, v)
        w = store.global_cfg.web
        return {"host": w.host, "has_password": bool(w.password),
                "bound": bound or w.host, "port": bound_port or w.port, "local": True}

    @app.get("/api/tts")
    async def tts_settings(slopgen: str | None = Cookie(default=None)) -> dict:
        """The voice engines, and which one is in use.

        Built from `tts.ENGINES` rather than a list written out here, because that
        registry exists precisely so a menu can be drawn without importing an engine —
        `qwen-local` costs a torch import and 2.3 GiB of weights, and no menu should
        cost that."""
        guard(slopgen)
        cfg = store.global_cfg.tts
        return {
            "engine": cfg.engine,
            "check_reference": cfg.check_reference,
            "engines": [
                {"id": e.id, "label": e.label, "description": e.description,
                 "gives_timings": e.gives_timings, "clones": e.clones,
                 "catalogue": e.catalogue,
                 "keys": [{"var": v, "set": bool(env_keys(v))} for v in e.key_envs],
                 "models": list(e.models), "packages": list(e.packages),
                 "presets": VOICE_PRESETS.get(e.id, {})}
                for e in TTS_ENGINES.values()
            ],
            # the clones are engine-independent: a card IS the voice, and any engine
            # that clones can speak with it (see config/README on configs/voices)
            "cloned": sorted(store.voices),
            "demo_text": DEMO_TEXT,
        }

    @app.put("/api/tts")
    async def save_tts(request: Request,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        b = await request.json()
        values: dict = {}
        if "engine" in b:
            engine = str(b["engine"])
            if engine not in TTS_ENGINES:
                raise HTTPException(status_code=404, detail=f"no engine {engine!r}")
            values["engine"] = engine
            store.global_cfg.tts.engine = engine
        if "check_reference" in b:
            values["check_reference"] = bool(b["check_reference"])
            store.global_cfg.tts.check_reference = values["check_reference"]
        if values:
            update_global("tts", values)
        return {"engine": store.global_cfg.tts.engine,
                "check_reference": store.global_cfg.tts.check_reference}

    def _cause(e: Exception) -> str:
        """The one line of an exception worth putting in a toast.

        A native-extension import failure is the reason for this: numpy answers a
        missing `libstdc++.so.6` with seven hundred characters of advice, and the
        sentence that says what is actually wrong is the last one. Showing the whole
        wall buries it, so the tail goes on screen and the traceback goes to the log —
        which is where a reader who needs the rest already knows to look."""
        text = " ".join(str(e).split())
        if len(text) <= 200:
            return text
        tail = [ln.strip() for ln in str(e).splitlines() if ln.strip()][-1]
        return tail[:400]

    # Audio content types by container. The browser decides whether it can play a
    # thing from this header, so guessing wrong is a silent failure to play.
    _DEMO_MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
                  ".flac": "audio/flac", ".m4a": "audio/mp4"}

    @app.post("/api/tts/demo")
    async def tts_demo(request: Request,
                       slopgen: str | None = Cookie(default=None)) -> Response:
        """Speak one line and hand back the audio ITSELF, not a link to it.

        Nothing is kept on this side. The take is synthesized into a temporary
        directory, read out, and the directory goes away with the request — the copy
        that survives is the one the browser holds, which is what makes "clear the
        cache" mean closing the tab rather than remembering to sweep a folder. The
        terminal's demo wrote takes into a shared temp directory and left them there,
        which is fine for a process the operator quits and wrong for a server that
        runs for days.

        Run in a worker thread, not on the event loop: a local take costs about a
        minute of CPU, and blocking here would stall every open run's event stream
        for that minute."""
        guard(slopgen)
        b = await request.json()
        engine = str(b.get("engine") or store.global_cfg.tts.engine)
        if engine not in TTS_ENGINES:
            raise HTTPException(status_code=404, detail=f"no engine {engine!r}")
        lang = str(b.get("lang") or store.global_cfg.ui.lang or "ru")
        voice = str(b.get("voice") or "").strip()
        if not voice:
            raise HTTPException(status_code=422, detail="pick a voice first")
        text = str(b.get("text") or "").strip() or DEMO_TEXT.get(lang, DEMO_TEXT["en"])

        def take() -> tuple[bytes, str]:
            import tempfile

            with tempfile.TemporaryDirectory(prefix="slopgen-demo-") as d:
                out = speak_demo(store, engine, voice, lang, text, Path(d))
                return out.read_bytes(), out.suffix

        try:
            data, suffix = await run_in_threadpool(take)
        except Exception as e:  # noqa: BLE001 — a missing key, missing weights, bad take
            log.exception("demo take failed (%s, %s)", engine, voice)
            raise HTTPException(status_code=502,
                                detail=f"{type(e).__name__}: {_cause(e)}") from e
        return Response(content=data,
                        media_type=_DEMO_MIME.get(suffix, "application/octet-stream"))

    @app.get("/api/voices")
    async def list_voices(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        guard(slopgen)
        return [_voice_json(v) for v in sorted(store.voices.values(), key=lambda v: v.name)]

    @app.get("/api/voices/{name}/sample")
    async def voice_sample(name: str, slopgen: str | None = Cookie(default=None)):
        """The sample itself, so a card can be listened to rather than only read."""
        guard(slopgen)
        v = store.voices.get(name)
        if v is None or v.ref_path is None or not v.ref_path.is_file():
            raise HTTPException(status_code=404, detail="this card has no sample")
        return FileResponse(v.ref_path)

    @app.put("/api/voices/{name}")
    async def save_voice(name: str, request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Edit a cloned voice's card — above all its transcript.

        `text` is what is said in the sample, typed by hand on purpose: lifting it off
        the audio with a recognizer was tried and the errors do not stay put, the model
        reconciling a wrong transcript with the audio by drifting (see VoiceConfig).
        So this field is the one that most wants a comfortable place to edit it."""
        guard(slopgen)
        v = store.voices.get(name)
        if v is None:
            raise HTTPException(status_code=404, detail=f"no voice named {name!r}")
        b = await request.json()
        for f in ("text", "ref_url", "lang", "description"):
            if f in b:
                setattr(v, f, str(b[f]))
        write_config("voices", name, v.model_dump(mode="json", exclude={"root"}))
        return _voice_json(v)

    def _report_json(r) -> dict:
        """A measurement in the shape the page draws it. `-inf` is a real answer here —
        digital silence under the voice — and JSON has no word for it, so it travels as
        None and is rendered as "тишина" rather than as a missing number."""
        def num(v):
            return None if v is None or v == float("-inf") else round(v, 1)
        return {"duration": round(r.duration, 2), "peak_db": num(r.peak_db),
                "rms_db": num(r.rms_db), "floor_db": num(r.floor_db),
                "silent_peak": r.peak_db == float("-inf"),
                "silent_floor": r.floor_db == float("-inf"),
                "summary": r.summary(), "usable": r.usable,
                "problems": [{"level": lv, "text": msg} for lv, msg in (r.problems or [])]}

    def _said_json(name: str, path: Path, text: str, lang: str) -> dict | None:
        """The strictest check there is: does the recording say what the transcript
        claims? A pair that disagrees is not a worse voice — it is a model that
        finishes the transcript out loud in the middle of a script.

        None when there is nothing to check with: no transcript typed yet, or no
        recogniser installed for that language. Skipped rather than installed, because
        a check that first downloads 46 MiB is a check people press once."""
        if not text.strip():
            return None
        from ..models import ModelStore
        from ..tts import align as aligner

        models = ModelStore(store.global_cfg.paths.models)
        model_id = aligner.model_for(lang or "ru", store.global_cfg.tts)
        if model_id not in models.installed():
            return None
        try:
            r = refs.check_transcript(path, text, models.require(model_id))
        except Exception:  # noqa: BLE001 — the strictest check, never the fatal one
            log.exception("transcript check failed for %r", name)
            return None
        return {"words": r.words, "found": r.found, "heard": round(r.heard, 3),
                "silent": round(r.silent, 3), "gap": round(r.gap, 2),
                "gap_at": round(r.gap_at, 2), "summary": r.summary(),
                "usable": r.usable,
                "problems": [{"level": lv, "text": msg} for lv, msg in (r.problems or [])]}

    def _rnnoise() -> Path:
        """The denoiser's weights, or a 422 naming the model to install.

        RNNoise and nothing else, which is measured rather than conventional: the
        obvious chain — spectral denoising plus loudness normalisation — made this
        project's own recordings worse, because `afftdn` leaves musical-noise artifacts
        that a cloner imitates faithfully and `loudnorm` lifts the noise floor along
        with the voice. See `tts/refs.py`."""
        from ..models import ModelStore

        try:
            return ModelStore(store.global_cfg.paths.models).require("rnnoise-sh") / "sh.rnnn"
        except Exception as e:  # noqa: BLE001 — not installed, and that is the message
            raise HTTPException(status_code=422, detail=str(e)) from e

    # What the upload box will take in. NOT what a sample may be on disk: everything
    # here is converted on the way in (see `new_voice`), which is the only reason the
    # list can be this wide.
    SAMPLE_SUFFIXES = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus", ".aac",
                       ".aiff", ".aif", ".wma", ".webm"}

    @app.post("/api/voices")
    async def new_voice(file: UploadFile, name: str = Form(...), text: str = Form(""),
                        lang: str = Form("ru"), description: str = Form(""),
                        clean: bool = Form(False),
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Take in a new cloned voice: the sample and the card, together.

        They are written as a pair because they are worthless apart — cloning here is
        zero-shot, so the (sample, transcript) pair IS the voice, and a card whose
        sample is missing clones nothing.

        The upload is CONVERTED rather than stored as it arrived, exactly as
        `slopgen voices add` has always done it (`tts.refs.convert`): mono, 24 kHz,
        rumble cut, peak-normalised WAV. Storing the file verbatim looked like it
        worked — the card saved, the player on the card played it, because the browser
        decodes far more than the engines do — and then cloning died on it, since the
        engines read samples with libsndfile, which knows WAV, FLAC, OGG and MP3 and
        has never heard of M4A or Opus. So a phone recording was accepted, listened
        back to, and only failed at the moment it was asked to speak.

        `clean` runs RNNoise over the sample. It stays a choice rather than something
        import does on its own, because denoising CHANGES the recording — so the reply
        carries the measurement from before and after, which is the only way to see
        whether it was worth doing. `/api/voices/{name}/clean` does the same to a
        sample already in the library."""
        guard(slopgen)
        if not name.strip() or "/" in name:
            raise HTTPException(status_code=422, detail="unusable voice name")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in SAMPLE_SUFFIXES:
            raise HTTPException(status_code=415, detail=f"{suffix or 'that'} is not audio")
        if not refs.have_ffmpeg():
            raise HTTPException(status_code=503, detail="ffmpeg is not on PATH")
        root = Path("configs/voices")
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{name}.wav"
        raw = root / f"{name}{suffix}.upload"
        with open(raw, "wb") as out:
            shutil.copyfileobj(file.file, out)
        rnnoise = _rnnoise() if clean else None
        try:
            # each of these is ffmpeg, and `convert` is two passes — off the event loop
            before = await run_in_threadpool(refs.inspect, raw)
            await run_in_threadpool(refs.convert, raw, dest, rnnoise)
        except Exception as e:  # noqa: BLE001 — a truncated upload, or not audio at all
            # A CalledProcessError says only what was run, which here is a hundred
            # characters of ffmpeg arguments and a temporary path — true, and no use to
            # anybody. What went wrong is on ffmpeg's stderr, and that goes to the log;
            # the operator gets the one thing they can act on.
            err = getattr(e, "stderr", b"") or b""
            log.error("could not convert the sample for %r: %s", name,
                      err.decode("utf-8", "replace").strip() or e)
            dest.unlink(missing_ok=True)  # never leave half a sample with no card
            raise HTTPException(
                status_code=422,
                detail=f"ffmpeg could not read {file.filename or 'that file'} as audio",
            ) from e
        finally:
            raw.unlink(missing_ok=True)
        # A card imported before under another container leaves its sample behind, and
        # a folder with both `марта.m4a` and `марта.wav` in it is a folder where the
        # next reader has to guess which one the card means.
        for old in root.glob(f"{name}.*"):
            if old != dest and old.suffix.lower() in SAMPLE_SUFFIXES:
                old.unlink(missing_ok=True)
        v = VoiceConfig(name=name, ref=dest.name, text=text, lang=lang,
                        description=description, root=root)
        write_config("voices", name, v.model_dump(mode="json", exclude={"root"}))
        store.voices[name] = v
        after = await run_in_threadpool(refs.inspect, dest)
        return {**_voice_json(v), "before": _report_json(before),
                "report": _report_json(after),
                "said": await run_in_threadpool(_said_json, name, dest, text, lang)}

    @app.post("/api/voices/{name}/check")
    async def check_voice(name: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """Measure the sample a card already holds, without changing it.

        Worth its own button because the numbers are what the denoiser's are compared
        against, and because a card imported long ago has never been measured at all —
        the checks used to run only in `slopgen voices add`."""
        guard(slopgen)
        v = store.voices.get(name)
        if v is None or v.ref_path is None or not Path(v.ref_path).is_file():
            raise HTTPException(status_code=404, detail="this card has no sample")
        path = Path(v.ref_path)
        return {"report": _report_json(await run_in_threadpool(refs.inspect, path)),
                "said": await run_in_threadpool(_said_json, name, path, v.text, v.lang)}

    @app.post("/api/voices/{name}/clean")
    async def clean_voice(name: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """Run RNNoise over the sample this card already holds, in place.

        This is the terminal's "import + denoise" pointed at a card instead of at a
        file on disk, and it is the common case: the recording is already in the
        library, it hisses, and re-finding the original on disk to import it again is
        busywork. The reply carries before and after, because that comparison IS the
        feature — a denoiser you cannot measure is one you have to take on faith.

        Cleaning twice is not the same as cleaning once, and nothing here prevents it:
        RNNoise is not idempotent, so a second pass keeps eating at what is left. The
        page says so rather than blocking it — an operator who wants another pass on a
        bad phone recording is not making a mistake."""
        guard(slopgen)
        v = store.voices.get(name)
        if v is None or v.ref_path is None or not Path(v.ref_path).is_file():
            raise HTTPException(status_code=404, detail="this card has no sample")
        if not refs.have_ffmpeg():
            raise HTTPException(status_code=503, detail="ffmpeg is not on PATH")
        path = Path(v.ref_path)
        rnnoise = _rnnoise()
        before = await run_in_threadpool(refs.inspect, path)
        # Via a temporary file: the source IS the destination here, and ffmpeg reading
        # and writing one file at once produces silence — the terminal learned this the
        # same way. The original is only replaced once the new one is written whole.
        tmp = path.with_name(path.name + ".cleaning.wav")
        try:
            await run_in_threadpool(refs.convert, path, tmp, rnnoise)
            tmp.replace(path)
        except Exception as e:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            err = getattr(e, "stderr", b"") or b""
            log.error("could not clean the sample for %r: %s", name,
                      err.decode("utf-8", "replace").strip() or e)
            raise HTTPException(status_code=422,
                                detail="ffmpeg could not clean this sample") from e
        after = await run_in_threadpool(refs.inspect, path)
        return {**_voice_json(v), "before": _report_json(before),
                "report": _report_json(after),
                "said": await run_in_threadpool(_said_json, name, path, v.text, v.lang)}

    @app.delete("/api/voices/{name}")
    async def remove_voice(name: str, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        v = store.voices.pop(name, None)
        if v is not None and v.ref_path and v.ref_path.is_file():
            v.ref_path.unlink()
        return {"deleted": delete_config("voices", name)}

    @app.get("/api/orchestrations")
    async def list_orchestrations(slopgen: str | None = Cookie(default=None)) -> dict:
        """The generator chains, and what may go in one.

        A chain is the one config that is a LIST of things rather than a set of
        fields, which is why it has its own pair of endpoints instead of riding the
        generic table: a form built out of flat fields cannot add a row."""
        guard(slopgen)
        return {
            "items": {n: c.model_dump(mode="json")
                      for n, c in sorted(store.orchestrations.items())},
            # `frames` and `search` are excluded for the reasons the TUI excludes them:
            # a chain names generators, and those two are not (see media/generate)
            "models": [m for m in list(VIDEO_MODELS) + list(PHOTO_MODELS)
                       if m not in ("search", "frames")],
            "metrics": ["clips", "seconds", "percent"],
            "key_modes": ["rotate", "single"],
        }

    @app.put("/api/orchestrations/{name}")
    async def save_orchestration(name: str, request: Request,
                                 slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        body = await request.json()
        body["name"] = name
        try:
            cfg = OrchestrationConfig.model_validate(body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))
        write_config("orchestration", name, cfg.model_dump(mode="json"))
        store.orchestrations[name] = cfg
        return cfg.model_dump(mode="json")

    @app.delete("/api/orchestrations/{name}")
    async def remove_orchestration(name: str,
                                   slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        store.orchestrations.pop(name, None)
        return {"deleted": delete_config("orchestration", name)}

    # -- local model weights -----------------------------------------------

    # A download of several gigabytes is not something to hold an HTTP request open
    # for, so installs run on the same pool the pipeline uses and report the same way.
    # There is one slot: two multi-gigabyte downloads at once is a way to make both
    # slow rather than a way to finish sooner.
    model_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slopgen-model")
    model_jobs: dict[str, dict] = {}

    def model_store() -> ModelStore:
        return ModelStore(Path(store.global_cfg.paths.models))

    @app.get("/api/models")
    async def list_models(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        guard(slopgen)
        ms = model_store()
        out = []
        for mid, spec in MODEL_CATALOG.items():
            job = model_jobs.get(mid)
            out.append({
                "id": mid, "label": spec.label, "description": spec.description,
                "license": spec.license, "used_by": spec.used_by,
                "packages": list(spec.packages),
                "size": sum(f.size for f in spec.files),
                "size_human": human_size(sum(f.size for f in spec.files)),
                "installed": ms.is_installed(mid),
                "on_disk": human_size(ms.disk_size(mid)) if ms.is_installed(mid) else "",
                "job": {k: job[k] for k in ("status", "label", "done", "total", "note")}
                       if job else None,
            })
        return out

    @app.post("/api/models/{model_id}")
    async def install_model(model_id: str,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if model_id not in MODEL_CATALOG:
            raise HTTPException(status_code=404, detail=f"no model {model_id!r}")
        job = model_jobs.get(model_id)
        if job and job["status"] in ("queued", "running"):
            return job
        job = model_jobs[model_id] = {"status": "queued", "label": "", "done": 0,
                                      "total": 0, "note": _t("js.queued")}

        def work() -> None:
            job.update(status="running", note="")
            try:
                model_store().install(
                    model_id,
                    progress=lambda label, done, total, note: job.update(
                        label=label, done=done, total=total, note=note),
                )
                job.update(status="done", note=_t("js.done"))
            except Exception as e:  # a failed download is a state, not a crash
                log.exception("model %s failed", model_id)
                job.update(status="failed", note=f"{type(e).__name__}: {e}")

        model_pool.submit(work)
        return job

    @app.delete("/api/models/{model_id}")
    async def remove_model(model_id: str,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        if model_id not in MODEL_CATALOG:
            raise HTTPException(status_code=404, detail=f"no model {model_id!r}")
        model_store().remove(model_id)
        model_jobs.pop(model_id, None)
        return {"deleted": True}

    # -- a world: its lore, its cast --------------------------------------

    @app.get("/api/worlds/{name}")
    async def world_detail(name: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """A world as the operator edits it: settings, lore files, cast.

        The cast is here rather than under `characters` because a world's character is
        not a person the way a drama's is — it is a LOOK, and it only means anything
        inside the world that has it (see `CharacterConfig`)."""
        guard(slopgen)
        w = world_or_404(name)
        lore = read_lore(w)
        return {
            "name": w.name, "tone": w.tone, "lore_tool": w.lore_tool,
            "docs": [d.name for d in fandom_docs(w)],
            "canon": w.canon,
            # the sheet is compiled from the documents and cached against their
            # checksum; saying so is the difference between "empty" and "stale"
            "canon_stale": bool(w.canon) and w.docs_sha != lore_sha(lore),
            "lore_chars": len(lore),
            "cast": [{"name": c.name, "appearance": c.appearance,
                      "plurality": c.plurality, "dirty": c.dirty,
                      "has_look": bool(c.visual_prompt)} for c in w.cast],
        }

    @app.get("/api/worlds/{name}/docs/{doc}")
    async def world_doc(name: str, doc: str,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        path = next((d for d in fandom_docs(w) if d.name == doc), None)
        if path is None:
            raise HTTPException(status_code=404, detail=f"no document named {doc!r}")
        return {"name": doc, "text": path.read_text(encoding="utf-8")}

    @app.put("/api/worlds/{name}/docs/{doc}")
    async def save_doc(name: str, doc: str, request: Request,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Write a lore document back.

        Nothing else has to happen: the canon sheet is checksummed against the
        documents, so editing one is what makes the sheet stale, and the next run
        rebuilds it. That is the same mechanism an outside markdown editor triggers,
        which is the point — lore is comfortably written elsewhere."""
        guard(slopgen)
        w = world_or_404(name)
        path = next((d for d in fandom_docs(w) if d.name == doc), None)
        if path is None:
            raise HTTPException(status_code=404, detail=f"no document named {doc!r}")
        body = await request.json()
        path.write_text(str(body.get("text", "")), encoding="utf-8")
        return {"ok": True, "stale": True}

    @app.put("/api/worlds/{name}/cast/{who}")
    async def save_character(name: str, who: str, request: Request,
                             slopgen: str | None = Cookie(default=None)) -> dict:
        """Edit one of a world's characters.

        Any change to the structured fields marks the entry dirty, which is what makes
        the next run recompile its `visual_prompt` — the English descriptor every
        picture request substitutes the name for. The old descriptor is kept until then
        rather than blanked: a stale look is still a look, and a run in the meantime
        should not lose the face."""
        guard(slopgen)
        w = world_or_404(name)
        c = next((x for x in w.cast if x.name == who), None)
        if c is None:
            raise HTTPException(status_code=404, detail=f"{who!r} is not in this world")
        body = await request.json()
        changed = False
        for field in ("appearance", "plurality"):
            if field in body and str(body[field]) != getattr(c, field):
                setattr(c, field, str(body[field]))
                changed = True
        if changed:
            c.dirty = True
        root = w.root / "characters"
        root.mkdir(parents=True, exist_ok=True)
        write_character(root / f"{c.name}.toml", c)
        return {"name": c.name, "appearance": c.appearance, "plurality": c.plurality,
                "dirty": c.dirty, "has_look": bool(c.visual_prompt)}

    # -- the wizard's AI help ----------------------------------------------
    #
    # Not the breakpoint rewrite. That one edits lines a run has already produced; this
    # writes the brief you launch WITH, while there is no run and no job to attach to.
    # The terminal's wizard has had both since the start (`llm/lore.write_brief` and
    # `llm/characters.autofill_all`); this is the browser asking the same two questions.

    # A world's own records where they fit, its compiled sheet where they do not. The
    # sheet is one line per thing, and one line is exactly where two similarly-named
    # institutions stop being distinguishable — so the records win whenever they can be
    # afforded. Same number the terminal uses.
    BRIEF_LORE_CHARS = 80_000

    def narration_budget(seconds: float, lang: str, rate: int) -> int:
        """How much this voice says in those seconds — zero when the length is free.

        Told rather than hidden: with no budget the length is read off the very brief
        the model is about to write, so a habitual five sentences would quietly decide
        how long the video runs."""
        from ..pipeline.drama import char_budget
        return char_budget(seconds, lang, rate) if seconds > 0 else 0

    @app.post("/api/ai/brief")
    async def ai_brief(request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Fandom: propose, or rewrite, what this video is about.

        It never touches the world's people. They are the world's, and inventing one
        here would be inventing a person into a place that does not have them."""
        guard(slopgen)
        b = await request.json()
        cfg = store.fandoms.get(str(b.get("fandom", "")))
        if cfg is None:
            raise HTTPException(status_code=404, detail="no such world")
        lore = read_lore(cfg)
        world = lore if lore and len(lore) <= BRIEF_LORE_CHARS else ((cfg.canon or "").strip() or lore)
        if not world:
            raise HTTPException(status_code=409,
                                detail="this world has nothing written down to read")
        lang = str(b.get("lang") or "en")
        seconds = float(b.get("duration_s") or 0.0)
        try:
            brief = lore_ai.write_brief(
                ChatLLM(store.active_llm_profile()), world,
                str(b.get("current", "")), str(b.get("instruction", "")), lang,
                duration_s=seconds,
                chars=narration_budget(seconds, lang, int(b.get("tts_rate") or 0)),
            )
        except Exception as e:
            log.exception("brief ai failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return {"brief": brief}

    @app.post("/api/ai/topic")
    async def ai_topic(request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Info: what this clip is about, in one sentence.

        The run invents this for itself when the idea is left blank — the point of
        asking here is to SEE it, and change it, before it becomes a video. Same
        prompt the stage uses, and the same history: topics already made in this niche
        and language are named so the model does not hand back one of them."""
        guard(slopgen)
        b = await request.json()
        lang = str(b.get("lang") or "en")
        kind = str(b.get("content_type") or "")
        ct = store.content_types.get(kind)
        brief = (ct.idea_brief.get(lang) or next(iter(ct.idea_brief.values()), "")) if ct else ""
        hist = store.global_cfg.paths.state / "history.json"
        try:
            recent = [h.get("topic", "") for h in json.loads(hist.read_text())[-30:]
                      if h.get("content_type") == kind and h.get("lang") == lang]
        except Exception:  # no history yet, or one written by a version that moved on
            recent = []
        try:
            topic = topic_ai.write_topic(ChatLLM(store.active_llm_profile()), lang, brief,
                                         recent, str(b.get("current", "")),
                                         str(b.get("instruction", "")))
        except Exception as e:
            log.exception("topic ai failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return {"topic": topic}

    @app.post("/api/ai/story")
    async def ai_story(request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Drama: the story polish — the plot, and who is in it.

        The browser's cast is a set of chips over the saved library, so of what the
        model may answer only two halves land here: the plot, and saved people it wants
        added. People it MAKES UP are reported by name and not created — a character
        invented as a side effect of pressing this would be a character the operator
        never agreed to keep."""
        guard(slopgen)
        b = await request.json()
        picked = [str(x) for x in (b.get("cast") or [])]
        library = [{"name": c.name, "age": c.age, "appearance": c.appearance}
                   for c in store.characters.values()]
        by_name = {c["name"]: c for c in library}
        cast = [by_name.get(n, {"name": n, "age": "", "appearance": ""}) for n in picked]
        try:
            res = char_ai.autofill_all(
                ChatLLM(store.active_llm_profile()), cast,
                str(b.get("lang") or "en"), str(b.get("scenario", "")),
                str(b.get("instruction", "")), library=library,
            )
        except Exception as e:
            log.exception("story ai failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        chosen = {n.casefold() for n in picked}
        add = [str(n) for n in (res.get("add_global") or [])
               if str(n) in by_name and str(n).casefold() not in chosen]
        invented = [str(r.get("name", "")).strip()
                    for r in (res.get("new_characters") or []) if isinstance(r, dict)]
        return {"scenario": str(res.get("scenario") or ""), "add": add,
                "invented": [n for n in invented if n and n not in by_name],
                "duration_min": res.get("recommended_duration_min")}

    # -- runs --------------------------------------------------------------

    @app.get("/api/runs")
    async def runs(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        guard(slopgen)
        # newest first, by whichever clock a run actually has: one adopted off disk
        # never started in this process, so its only timestamp is when it last wrote
        out = []
        for r in sorted(sup.runs.values(), key=lambda r: -max(r.started_at, r.finished_at)):
            d = r.as_dict()
            # the ad-hoc chain is excluded from the params dump (it is a whole config,
            # not a setting), but WHICH generator a run is using is the one thing about
            # it worth seeing in a list
            chain = r.params.manual_orchestration
            d["source"] = chain.stages[0].model if chain and chain.stages else ""
            d["parked"] = parked(r) if r.status not in ("running", "queued") else {
                "review_stage": "", "asks": 0, "video": False}
            out.append(d)
        return out

    # -- starting a run ----------------------------------------------------
    #
    # Three lines each, because what a form MEANS lives in web/params.py — the chat
    # reads the same functions, so a run started by a button in Telegram and one
    # started by this form are the same run built the same way.

    @app.post("/api/runs/fandom")
    async def start_fandom(request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Start a fandom run, or a loop of them."""
        guard(slopgen)
        b = await request.json()
        params = fandom_params(store, b)
        title = str(b.get("title", "")) or f'{_t("web.mode.fandom")} · {params.fandom}'
        loop = loop_of(b)
        return sup.start_loop(params, title, **loop).as_dict() if loop \
            else sup.start(params, title=title).as_dict()

    @app.post("/api/runs/info")
    async def start_info(request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Start an info run, or a loop of them."""
        guard(slopgen)
        b = await request.json()
        params = info_params(store, b)
        title = str(b.get("title", "")) or _t("web.mode.info")
        loop = loop_of(b)
        return sup.start_loop(params, title, **loop).as_dict() if loop \
            else sup.start(params, title=title).as_dict()

    @app.post("/api/runs/drama")
    async def start_drama(request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Start a drama run, or a loop of them."""
        guard(slopgen)
        b = await request.json()
        params = drama_params(store, b)
        title = str(b.get("title", "")) or _t("web.mode.drama")
        loop = loop_of(b)
        return sup.start_loop(params, title, **loop).as_dict() if loop \
            else sup.start(params, title=title).as_dict()

    @app.post("/api/runs")
    async def start_run(request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Start a run and hand back its id, immediately.

        The reply does not wait for anything: the run goes to the pool and the page
        goes back to whatever it was doing. That is the whole difference from the
        terminal, where starting a run and watching it are the same act."""
        guard(slopgen)
        body = await request.json()
        try:
            params = store.resolve_params(body) if hasattr(store, "resolve_params") \
                else RunParams.model_validate(body)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"bad run parameters: {e}")
        run = sup.start(params, title=str(body.get("title", "")))
        return run.as_dict()

    @app.get("/api/loops")
    async def loops(slopgen: str | None = Cookie(default=None)) -> list[dict]:
        """Every loop this server started, newest first. A loop is read off its plan
        file on each request rather than remembered: a terminal may have steered it
        since, and the file is the loop."""
        guard(slopgen)
        out = []
        for lp in sorted(sup.loops.values(), key=lambda x: -x.started_at):
            try:
                out.append(lp.as_dict())
            except Exception:  # its folder was deleted under us; not worth a 500
                continue
        return out

    @app.put("/api/loops/{loop_id}")
    async def steer_loop(loop_id: str, request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Change a running loop: who picks the topics, how many are left, which stages
        stop for review, what a parked video means. Every field is optional and every
        one of them lands on the NEXT video."""
        guard(slopgen)
        b = await request.json()
        fields: dict = {}
        if b.get("source") in ("ai", "me"):
            fields["source"] = b["source"]
        if b.get("on_park") in ("hold", "go_on"):
            fields["on_park"] = b["on_park"]
        if "limit" in b:
            fields["limit"] = max(0, int(b.get("limit") or 0))
        if "ahead" in b:
            fields["ahead"] = max(0, min(int(b.get("ahead") or 0), 50))
        if isinstance(b.get("topics"), list):
            fields["topics"] = list(b["topics"])
        if isinstance(b.get("add_topics"), list):
            fields["add_topics"] = list(b["add_topics"])
        if isinstance(b.get("breakpoints"), list):
            loop = sup.loops.get(loop_id)
            mode = loop.params.mode if loop else "info"
            fields["breakpoints"] = [str(x) for x in b["breakpoints"]
                                     if str(x) in review.available(mode)]
        loop = sup.edit_loop(loop_id, **fields)
        if loop is None:
            raise HTTPException(status_code=404, detail="no such loop")
        return loop.as_dict()

    @app.put("/api/loops/{loop_id}/params")
    async def retune_loop(loop_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Rewrite everything a loop's next video is made on.

        The body is the mode's own start form, unchanged — the page fills that form
        from the loop and sends it back here instead of to `/api/runs/<mode>`, so
        "every setting" means literally the same set either way and neither door can
        drift from the other. What a loop may not change about itself (its mode, its
        count, its output folder, the topic — that is the queue's) is taken back off
        the result by `loop.retune`, so a form carrying those cannot smuggle them in."""
        guard(slopgen)
        loop = sup.loops.get(loop_id)
        if loop is None:
            raise HTTPException(status_code=404, detail="no such loop")
        b = await request.json()
        build = {"info": info_params, "drama": drama_params,
                 "fandom": fandom_params}[loop.params.mode]
        params = build(store, b)
        problems = check_params(store, params)
        if problems:  # a name no config has; the run would fail hours from now
            raise HTTPException(status_code=422, detail="; ".join(problems))
        if loop_of(b):  # the loop card sends the loop block back with the form
            sup.edit_loop(loop_id, **{k: v for k, v in loop_of(b).items()
                                      if k != "topics"})
        return sup.retune_loop(loop_id, params).as_dict()

    # -- the queue ---------------------------------------------------------
    #
    # Three doors onto one list, because they are three different acts. Replacing it is
    # reordering, retyping and removing — everything the operator does by hand to the
    # list they are looking at. Patching it is the bulk edit, which exists precisely so
    # that changing one setting on six videos does not mean sending six whole videos
    # back. And stocking it is asking the model, which is the only one of the three that
    # can take ten seconds and fail.

    def loop_or_404(loop_id: str):
        loop = sup.loops.get(loop_id)
        if loop is None:
            raise HTTPException(status_code=404, detail="no such loop")
        return loop

    @app.put("/api/loops/{loop_id}/queue")
    async def set_queue(loop_id: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Replace the queue with the list the page is showing.

        Entries carry ids, so this says what the queue IS rather than what changed about
        it — which is what makes reordering, retyping and deleting one operation instead
        of three, and what keeps an edit from landing on the wrong video when the runner
        took one off the front while the page was being read."""
        guard(slopgen)
        loop = loop_or_404(loop_id)
        items = (await request.json()).get("items")
        if not isinstance(items, list):
            raise HTTPException(status_code=422, detail="a queue is a list of entries")
        try:
            loop.file.set_queue(items)
        except (ValueError, ValidationError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        return loop.as_dict()

    @app.patch("/api/loops/{loop_id}/queue")
    async def patch_queue(loop_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Set (or clear) ONE setting on several queued videos, leaving everything else
        about each of them alone. See `LoopFile.patch_queue` for why bulk editing is
        shaped as a field and a value rather than as a form."""
        guard(slopgen)
        loop = loop_or_404(loop_id)
        b = await request.json()
        ids = [str(i) for i in (b.get("ids") or [])]
        if not ids:
            raise HTTPException(status_code=422, detail="no videos named")
        try:
            loop.file.patch_queue(ids, b.get("set") or {}, b.get("clear") or [])
        except (ValueError, ValidationError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        return loop.as_dict()

    @app.post("/api/loops/{loop_id}/queue/ai")
    async def stock_queue(loop_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Ask the model for topics now, and put them in the queue as ordinary entries —
        to be read, rewritten, reordered or thrown away like any other."""
        guard(slopgen)
        loop_or_404(loop_id)
        n = max(1, min(int((await request.json()).get("n") or 1), 5))
        try:
            # off the request thread: this is one or more model calls, and holding the
            # server's event loop for them stops every other page on it
            loop = await asyncio.get_running_loop().run_in_executor(
                None, sup.stock_loop, loop_id, n)
        except Exception as e:
            log.exception("loop topics failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return loop.as_dict()

    @app.post("/api/loops/{loop_id}/stop")
    async def stop_loop(loop_id: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """Stop making new videos. The one being made now is left to finish — it has a
        stop of its own in the runs list, and tearing it in half is a different act."""
        guard(slopgen)
        return {"ok": sup.stop_loop(loop_id)}

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Pick a parked run back up — the one on disk, by its folder.

        Runs park constantly in this pipeline (waiting for pictures, waiting for a
        review), and a parked run is not a failure but a message. Resuming is the
        reply to it."""
        guard(slopgen)
        body = await request.json()
        rd = Path(str(body.get("run_dir", "")))
        if not (rd / "checkpoint.json").is_file():
            raise HTTPException(status_code=404, detail=f"no run parked at {rd}")
        return sup.resume(rd).as_dict()

    @app.post("/api/runs/{run_id}/stop")
    async def stop_run(run_id: str, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        return {"ok": sup.stop(run_id)}

    def run_or_404(run_id: str):
        run = sup.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        return run

    @app.delete("/api/runs/{run_id}")
    async def forget_run(run_id: str,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Take a settled run off the list and delete its folder.

        There was no way to do this at all, which showed up the moment somebody
        launched the same run twice: stopping the second one leaves it sitting in the
        list forever, offering to resume something nobody wants resumed.

        This deletes real output — a finished video lives in that folder — so the page
        asks twice before calling it."""
        guard(slopgen)
        try:
            removed = sup.forget(run_id, Path(store.global_cfg.paths.output))
        except KeyError:
            raise HTTPException(status_code=404, detail="no such run") from None
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        return {"deleted": True, "removed": removed}

    @app.get("/api/runs/{run_id}/asks")
    async def run_asks(run_id: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """What a parked run is waiting for, read off the manifest on disk.

        The manifest is the authority, not this server: a run parks by writing it, the
        frozen TUI reads the same file, and `slopgen gather` works whether or not
        anything is serving HTTP. All this does is show it and take the files."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            return {"status": run.status, "shots": []}
        out = []
        for work in sorted(p for p in run.run_dir.iterdir() if p.is_dir()):
            path = manual.manifest_path(work)
            if not path.is_file():
                continue
            mf = manual.ManualManifest.model_validate_json(path.read_text(encoding="utf-8"))
            for sh in mf.shots:
                out.append({"video": work.name, "id": sh.id, "prompt": manual.task_text(sh),
                            "status": sh.status, "want": sh.want, "kind": sh.kind,
                            "size": [sh.width, sh.height], "target_s": sh.target_s,
                            "photo": sh.photo,
                            "delivered": bool(sh.clip and Path(sh.clip).is_file())})
        # what is still owed first: a drama can owe two hundred pictures and have
        # delivered fifty, and scrolling past the finished ones to find the work is
        # the wrong way round
        out.sort(key=lambda sh: (sh["status"] == "delivered", sh["video"], sh["id"]))
        return {"status": run.status, "message": run.message, "shots": out,
                "pending": sum(1 for sh in out if sh["status"] != "delivered")}

    @app.post("/api/runs/{run_id}/asks/{video}/{shot_id}")
    async def deliver_ask(run_id: str, video: str, shot_id: str, file: UploadFile,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Drop one picture into the run's inbox, from the browser.

        It lands under the shot's own id, which is exactly what `manual.scan_inbox`
        looks for — so a file handed over here and a file copied in by hand are the
        same thing, and neither needs this server to be running afterwards."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=409, detail="this run has no folder yet")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise HTTPException(status_code=415, detail=f"{suffix or 'that'} is not a picture or a clip")
        inbox = manual.inbox_dir(run.run_dir / video)
        inbox.mkdir(parents=True, exist_ok=True)
        dest = inbox / f"{shot_id}{suffix}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        return {"ok": True, "at": str(dest)}

    @app.get("/api/runs/{run_id}/asks/{video}/{shot_id}/file")
    async def ask_file(run_id: str, video: str, shot_id: str,
                       slopgen: str | None = Cookie(default=None)):
        """What was handed over for this shot.

        Delivered material is worth SEEING, not just being told about: a wrong file
        under the right name looks identical in a manifest, and a drama can owe two
        hundred of them."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=404, detail="this run has no folder")
        path = manual.manifest_path(run.run_dir / video)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no manifest for that video")
        mf = manual.ManualManifest.model_validate_json(path.read_text(encoding="utf-8"))
        shot = next((s for s in mf.shots if s.id == shot_id), None)
        if shot is None or not shot.clip or not Path(shot.clip).is_file():
            raise HTTPException(status_code=404, detail="nothing delivered for that shot")
        return FileResponse(Path(shot.clip))

    @app.delete("/api/runs/{run_id}/asks/{video}/{shot_id}")
    async def undeliver(run_id: str, video: str, shot_id: str,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Take a delivery back, so a wrong file can be replaced.

        The manifest is the authority on what has arrived, so putting a shot back to
        `pending` there is the whole of it — the next scan picks up whatever is dropped
        in next. The file itself is left alone: it is something somebody made, and this
        is an undo, not a bin."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=404, detail="this run has no folder")
        work = run.run_dir / video
        path = manual.manifest_path(work)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no manifest for that video")
        mf = manual.ManualManifest.model_validate_json(path.read_text(encoding="utf-8"))
        shot = next((s for s in mf.shots if s.id == shot_id), None)
        if shot is None:
            raise HTTPException(status_code=404, detail=f"no shot {shot_id!r}")
        shot.status, shot.clip, shot.photo = "pending", None, False
        mf.save(work)
        return {"ok": True, "id": shot_id}

    @app.get("/api/runs/{run_id}/review")
    async def read_review(run_id: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """The document a parked breakpoint is showing.

        `review.read` already returns a UI-agnostic list of rows with a kind and, where
        it is a choice, its options — which is a form schema in everything but name, so
        the browser renders it the same way the terminal does without either knowing
        about the other."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=409, detail="this run has no folder yet")
        cp = Checkpoint.load(run.run_dir)
        for i in range(run.params.count):
            stage = cp.review_stage(i)
            if not stage:
                continue
            job = cp.load_job(i)
            if job is None:
                continue
            doc = review.read(stage, job, run.params.mode)
            # `review._picture_doc` can only offer the cards the plan already uses: it
            # is handed a job and nothing else, and a job does not know which world it
            # came from. That is exactly backwards on the run that needs it most — one
            # where the base covered nothing, so the list is empty and there is nothing
            # to pin. Here the world IS known, so the choice becomes the whole base.
            if stage == "picture":
                world = store.fandoms.get(run.params.fandom)
                names = sorted(c.name for c in (world.frames if world else []) if c.usable)
                for r in doc.rows:
                    if r.kind == "choice":
                        r.options = names
            return {"video": i, "stage": stage,
                    # what the AI edit line needs to know about this document
                    "subject": doc.subject, "variable": doc.variable, "rows": [
                {"label": r.label, "value": r.value, "src": r.src, "info": r.info,
                 "readonly": r.readonly, "field": r.field, "kind": r.kind,
                 "options": r.options} for r in doc.rows]}
        return {"video": -1, "stage": "", "rows": []}

    @app.post("/api/runs/{run_id}/review/ai")
    async def review_ai(run_id: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """The AI edit line: the lines being reviewed plus one instruction, edited whole.

        Only free-text rows go to the model — a cast chip set, a generator choice or a
        clip length are not prose and must not be "rewritten". A mixed document tells
        the model what each line IS (`kinds`), because a shot prompt and a spoken line
        want opposite things and swapping their forms is the failure this prevents."""
        guard(slopgen)
        run = sup.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")
        b = await request.json()
        instruction = str(b.get("instruction", "")).strip()
        if not instruction:
            raise HTTPException(status_code=400, detail="say what to change")
        rows = list(b.get("rows", []))
        editable = [i for i, r in enumerate(rows)
                    if not r.get("readonly") and r.get("kind", "text") == "text"]
        if not editable:
            return {"rows": rows, "changed": 0}
        fields = [str(rows[i].get("field", "text")) for i in editable]
        try:
            out = bp_ai.rewrite(
                ChatLLM(store.active_llm_profile()),
                [str(rows[i].get("value", "")) for i in editable], instruction,
                lang=run.params.lang, subject=str(b.get("subject", "lines")),
                variable=bool(b.get("variable")),
                kinds=fields if any(f != "text" for f in fields) else None,
            )
        except Exception as e:
            log.exception("review ai failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        if not out:
            return {"rows": rows, "changed": 0}
        # A variable document may come back longer or shorter; a fixed one may not, and
        # the model is told so — but the caller still only trusts what it can place.
        for n, i in enumerate(editable):
            if n < len(out):
                rows[i]["value"] = out[n]
        return {"rows": rows, "changed": min(len(out), len(editable))}

    @app.post("/api/runs/{run_id}/review")
    async def apply_review(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Fold the edited rows back and let the run go on.

        `review.apply` answers whether the edit made the stage's own output stale; when
        it did, the stage is struck off the completed list so the resume re-runs it.
        That is the same answer the terminal acts on — the logic lives in the pipeline,
        and both frontends only carry rows to it and back."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=409, detail="this run has no folder yet")
        b = await request.json()
        i, stage = int(b.get("video", 0)), str(b.get("stage", ""))
        cp = Checkpoint.load(run.run_dir)
        job = cp.load_job(i)
        if job is None or not stage:
            raise HTTPException(status_code=409, detail="nothing is parked for review here")
        rows = [review.Row(label=str(r.get("label", "")), value=str(r.get("value", "")),
                           src=r.get("src"), field=str(r.get("field", "text")),
                           kind=str(r.get("kind", "text")), info=str(r.get("info", "")),
                           options=list(r.get("options", [])))
                for r in b.get("rows", []) if isinstance(r, dict)]
        stale = review.apply(stage, job, rows, run.params.mode)
        cp.review_done(job, cp.completed(i), stage, rerun=bool(stale))
        return {"ok": True, "rerun": bool(stale)}

    @app.get("/api/runs/{run_id}/video")
    async def run_video(run_id: str, slopgen: str | None = Cookie(default=None)):
        """The finished cut, so a done run is something you can actually look at
        rather than a green pill saying it went well."""
        guard(slopgen)
        run = run_or_404(run_id)
        if run.run_dir is None:
            raise HTTPException(status_code=404, detail="this run has no folder")
        cuts = sorted(run.run_dir.glob("*/*.mp4"), key=lambda p: p.stat().st_mtime)
        if not cuts:
            raise HTTPException(status_code=404, detail="this run produced no video")
        return FileResponse(cuts[-1], media_type="video/mp4")

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = 0,
                         slopgen: str | None = Cookie(default=None)) -> StreamingResponse:
        guard(slopgen)
        run = sup.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="no such run")

        async def stream():
            q = sup.subscribe(run)
            try:
                # the backlog first, then the live feed: this is what makes closing
                # the tab harmless rather than merely survivable
                for ev in sup.backlog(run, after):
                    yield f"data: {json.dumps(ev.as_dict(), ensure_ascii=False)}\n\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=20)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"  # proxies drop a silent stream
                        continue
                    yield f"data: {json.dumps(ev.as_dict(), ensure_ascii=False)}\n\n"
            finally:
                sup.unsubscribe(run, q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    # -- the page ----------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((HERE / "static" / "index.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store"})

    app.state.supervisor = sup
    return app


# The API keys slopgen actually reads, and what each buys. `.env` may hold anything;
# these are the ones a form has a reason to ask about.

# What each key buys. The blurb is a label KEY, not the text: this list is read once
# at import, while the interface language is a setting the operator changes at any
# time, so the words have to be looked up when they are shown, not when they are
# listed.
def _described(entries: dict, *fields: str, key: str = "") -> list:
    """`[{"v": name, "note": ...}]`, or a bare name where there is nothing to add. The
    browser renders the pair; the value it submits is the name either way.

    Only a STRING counts as a note. `AdConfig.description` is a nested object holding
    the snippet, and stringifying it puts `snippet='...'` in a dropdown."""
    out = []
    for name in sorted(entries):
        note = ""
        for field in fields or ("description",):
            value = getattr(entries[name], field, None)
            if isinstance(value, str) and value.strip():
                note = value.strip()
                break
        if not note:
            out.append(name)
            continue
        # A profile slopgen ships has its blurb in our own table, so it follows the
        # interface language; a world or a contract the operator wrote keeps the words
        # they wrote. The key is sent either way and simply misses for the latter.
        out.append({"v": name, "note": note, "key": f"{key}.{name}"} if key
                   else {"v": name, "note": note})
    return out


KEY_VARS = [
    ("DEEPSEEK_API_KEY", "key.deepseek"),
    ("GEMINI_API_KEY", "key.gemini"),
    ("OPENROUTER_API_KEY", "key.openrouter"),
    ("LLM_API_KEY", "key.llm"),
    ("POLLINATIONS_TOKEN", "key.pollinations"),
    ("HF_TOKEN", "key.hf"),
    ("PEXELS_API_KEY", "key.pexels"),
    ("PIXABAY_API_KEY", "key.pixabay"),
]


def _voice_json(v) -> dict:
    p = v.ref_path
    return {"name": v.name, "text": v.text, "lang": v.lang, "ref": v.ref,
            "ref_url": v.ref_url, "description": v.description,
            "has_sample": bool(p and p.is_file()),
            "seconds": _audio_seconds(p) if p and p.is_file() else 0.0,
            "url": f"/api/voices/{v.name}/sample"}


def _audio_seconds(path: Path) -> float:
    try:
        from ..media.ffmpeg import duration_of

        return round(duration_of(path), 1)
    except Exception:
        return 0.0


def _fields(model) -> list[dict]:
    """A config's editable shape, taken off the pydantic model rather than written out
    by hand — so a field added to a model appears in the form without anybody
    remembering to add it twice."""
    out = []
    for name, f in model.model_fields.items():
        if name == "name" or f.exclude:
            continue
        ann = f.annotation
        kind, options = "text", []
        origin = getattr(ann, "__origin__", None)
        if ann is bool:
            kind = "bool"
        elif ann in (int, float):
            kind = "number"
        elif getattr(ann, "__name__", "") == "Literal" or str(origin) == "typing.Literal":
            kind, options = "choice", [str(a) for a in getattr(ann, "__args__", ())]
        elif str(ann).startswith("typing.Literal"):
            kind, options = "choice", [str(a) for a in getattr(ann, "__args__", ())]
        elif origin is list:
            kind = "list"
        elif origin is dict:
            kind = "map"
        out.append({"name": name, "kind": kind, "options": options,
                    "help": (f.description or "")})
    return out


def _card_json(world: str, c: FrameCard) -> dict:
    p = c.path
    return {
        "name": c.name, "file": c.file, "prompt": c.prompt,
        "description": c.description, "note": c.note, "retired": c.retired,
        "usable": c.usable,
        "kind": "video" if (p and p.suffix.lower() in VIDEO_EXTS) else "image",
        "url": f"/api/worlds/{world}/cards/{c.name}/file",
        "targets": [{"label": t.label, "of": t.of,
                     "cx": t.rect.cx, "cy": t.rect.cy, "scale": t.rect.scale}
                    for t in c.targets],
    }


def _target(t: dict) -> CropTarget:
    return CropTarget(
        label=str(t.get("label", "")), of=str(t.get("of", "")),
        rect=Rect(cx=float(t.get("cx", 0.5)), cy=float(t.get("cy", 0.5)),
                  scale=float(t.get("scale", 1.0))).clamped(),
    )


def _free_stem(text: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[^\w \-]", "", text).strip()[:40].strip() or "кадр"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def serve(store: ConfigStore) -> None:
    """Run the server. Refuses to leave loopback without a password, because the same
    server starts runs and spends quota."""
    import uvicorn

    cfg = store.global_cfg.web
    host = cfg.host
    if not cfg.password and host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("web.host is %s but no web.password is set — staying on loopback", host)
        host = "127.0.0.1"
    uvicorn.run(create_app(store, bound=host, bound_port=cfg.port),
                host=host, port=cfg.port, log_level="warning")
