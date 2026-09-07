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

from fastapi import Cookie, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from ..config import ConfigStore, RunParams
from ..config.loader import (delete_config, fandom_docs, file_sha, frames_dir,
                             lore_sha, read_lore, update_global, write_character,
                             write_config, write_frame_card)
from ..config.envfile import set_env_var
from ..llm.client import MODEL_PRESETS, PROVIDERS
from .. import labels
from ..media.filters import CATALOGUE as FILTER_CATALOGUE
from ..media.generate import (PHOTO_MODELS, VIDEO_MODELS, env_keys,
                              model_clip_seconds)
from ..tts import ENGINES as TTS_ENGINES
from ..config.models import (AccountConfig, AdConfig, CharacterConfig, CropTarget,
                             FrameCard, LLMProfile, OrchestrationConfig,
                             OrchestrationConfig, OrchestrationStage, PresetConfig,
                             Rect, VisualsConfig,
                             VoiceConfig)
from ..media.stock import IMAGE_EXTS, VIDEO_EXTS
from ..pipeline import manual, review
from ..pipeline.checkpoint import Checkpoint
from ..models import CATALOG as MODEL_CATALOG
from ..models import ModelStore, human_size
from .runs import Supervisor

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

ALLOWED_SUFFIXES = IMAGE_EXTS | VIDEO_EXTS


def create_app(store: ConfigStore) -> FastAPI:
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

    def guard(token: str | None) -> None:
        if not cfg.password:
            return
        if token not in sessions:
            raise HTTPException(status_code=401, detail="not signed in")

    @app.post("/api/login")
    async def login(password: str = Form(...)) -> JSONResponse:
        if not cfg.password or not secrets.compare_digest(password, cfg.password):
            raise HTTPException(status_code=401, detail="wrong password")
        token = secrets.token_urlsafe(32)
        sessions.add(token)
        r = JSONResponse({"ok": True})
        r.set_cookie("slopgen", token, httponly=True, samesite="lax")
        return r

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
        return {
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
                "signed_in": not cfg.password or slopgen in sessions}

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
                 "models": list(e.models), "packages": list(e.packages)}
                for e in TTS_ENGINES.values()
            ],
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

    @app.post("/api/voices")
    async def new_voice(file: UploadFile, name: str = Form(...), text: str = Form(""),
                        lang: str = Form("ru"), description: str = Form(""),
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Take in a new cloned voice: the sample and the card, together.

        They are written as a pair because they are worthless apart — cloning here is
        zero-shot, so the (sample, transcript) pair IS the voice, and a card whose
        sample is missing clones nothing."""
        guard(slopgen)
        if not name.strip() or "/" in name:
            raise HTTPException(status_code=422, detail="unusable voice name")
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus"}:
            raise HTTPException(status_code=415, detail=f"{suffix or 'that'} is not audio")
        root = Path("configs/voices")
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{name}{suffix}"
        with open(dest, "wb") as out:
            shutil.copyfileobj(file.file, out)
        v = VoiceConfig(name=name, ref=dest.name, text=text, lang=lang,
                        description=description, root=root)
        write_config("voices", name, v.model_dump(mode="json", exclude={"root"}))
        store.voices[name] = v
        return _voice_json(v)

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

    # -- runs --------------------------------------------------------------

    def parked(run) -> dict:
        """What this run is actually waiting for, read off its own folder.

        A status is not enough to decide what may be done with a run. "stopped" says
        nothing about whether it was sitting on a breakpoint when it stopped, and
        "paused" says nothing about how many pictures are still owed. Offering every
        action to every settled run was the first version, and it meant most buttons
        did nothing when pressed — which reads as a broken page rather than as an
        answer.

        The checkpoint is small and this is cached against its mtime, so a list of
        forty runs costs forty stat calls and nothing else."""
        if run.run_dir is None:
            return {"review_stage": "", "asks": 0, "video": False}
        cp_file = run.run_dir / "checkpoint.json"
        try:
            stamp = cp_file.stat().st_mtime
        except OSError:
            return {"review_stage": "", "asks": 0, "video": False}
        cached = getattr(run, "_parked", None)
        if cached and cached[0] == stamp:
            return cached[1]
        info = {"review_stage": "", "asks": 0, "video": False}
        try:
            cp = Checkpoint.load(run.run_dir)
            for i in range(run.params.count):
                info["review_stage"] = info["review_stage"] or cp.review_stage(i)
        except Exception:
            pass
        for work in (p for p in run.run_dir.iterdir() if p.is_dir()):
            mp = manual.manifest_path(work)
            if mp.is_file():
                try:
                    mf = manual.ManualManifest.model_validate_json(mp.read_text(encoding="utf-8"))
                    info["asks"] += sum(1 for sh in mf.shots if sh.status != "delivered")
                except Exception:
                    pass
            info["video"] = info["video"] or any(work.glob("*.mp4"))
        run._parked = (stamp, info)
        return info

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

    @app.post("/api/runs/fandom")
    async def start_fandom(request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Start a fandom run.

        The chain is built here rather than named, because a fandom's picture comes
        from ONE source for the whole video — `frames` most of all, which is
        all-or-nothing by construction (see framebase.active). So the form picks the
        source and this turns it into a one-stage chain, which is what the pipeline
        reads. Hardcoding `frames` was the first version, and it left the old
        per-shot modes unreachable from the browser entirely."""
        guard(slopgen)
        b = await request.json()
        world = str(b.get("fandom", ""))
        if world not in store.fandoms:
            raise HTTPException(status_code=404, detail=f"no world named {world!r}")
        medium = b.get("medium", "photo")
        source = str(b.get("source") or ("frames" if medium == "photo" else "wan2.1"))
        allowed = (list(PHOTO_MODELS) + ["manual", "search"]) if medium == "photo" \
            else list(VIDEO_MODELS)
        if source not in allowed:
            raise HTTPException(status_code=422,
                                detail=f"{source!r} does not make {medium}")
        params = RunParams(
            lang=str(b.get("lang", "ru")), content_type="", mode="fandom",
            fandom=world, fandom_voice=b.get("voice", "resident"), medium=medium,
            scenario=str(b.get("scenario", "")),
            duration_s=float(b.get("duration_s", 45.0)),
            count=int(b.get("count", 1)),
            dry_run=bool(b.get("dry_run", True)),
            breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
            frame_fit=b.get("frame_fit", "close"),
            cut_sensitivity=float(b.get("cut_sensitivity", 0.35)),
            **_common(b),
            manual_orchestration=OrchestrationConfig(
                name=source,
                stages=[OrchestrationStage(model=source, metric="percent", amount=100.0,
                                           clip_seconds=model_clip_seconds(source))]),
        )
        return sup.start(params, title=str(b.get("title", "")) or f'{_t("web.mode.fandom")} · {world}').as_dict()

    def _common(b: dict) -> dict:
        """The settings every mode shares, read off one block rather than three.

        They were missing from the browser entirely, and `filters` is the one that
        mattered most: the montage look — grain, tape, tube, glitch — is most of how
        this genre reads, and it is the only picture control that works in every mode
        and from every source, because it is asked of ffmpeg rather than of a model."""
        out: dict = {
            "profanity": int(b.get("profanity", 0)),
            "ad": str(b.get("ad", "")),
            "ad_mode": b.get("ad_mode", "both"),
            "push": str(b.get("push", "")),
            "visual_notes": str(b.get("visual_notes", "")),
            "visual_style": str(b.get("visual_style", "")),
            "clean_subtitles": bool(b.get("clean_subtitles", False)),
            "voice_override": str(b.get("voice_override", "")),
            "tts_engine": str(b.get("tts_engine", "")),
            "tts_rate": int(b.get("tts_rate", 0)),
            "keep_temp": bool(b.get("keep_temp", False)),
            "filters": {k: max(0, min(100, int(v)))
                        for k, v in (b.get("filters") or {}).items()
                        if k in FILTER_HELP and int(v) > 0},
        }
        if b.get("subtitle_style"):
            out["subtitle_style"] = b["subtitle_style"]
        return out

    @app.post("/api/runs/info")
    async def start_info(request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """The minute-of-useless-info clip: a topic, or none and the model invents one."""
        guard(slopgen)
        b = await request.json()
        params = RunParams(
            lang=str(b.get("lang", "ru")),
            content_type=str(b.get("content_type", "")),
            mode="info", idea=str(b.get("idea", "")),
            visuals=str(b.get("visuals", "classic")),
            duration_s=float(b.get("duration_s", 45.0)),
            count=int(b.get("count", 1)),
            dry_run=bool(b.get("dry_run", True)),
            breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
            **_common(b),
        )
        return sup.start(params, title=str(b.get("title", "")) or _t("web.mode.info")).as_dict()

    @app.post("/api/runs/drama")
    async def start_drama(request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """The AI drama: a premise, a cast, and a generator chain.

        The chain is the one thing this mode cannot default sensibly — it is what the
        operator is rationing free tiers with — so it is named, and an unknown name is
        refused here rather than silently falling back three stages later."""
        guard(slopgen)
        b = await request.json()
        orch = str(b.get("orchestration", ""))
        if orch and orch not in store.orchestrations:
            raise HTTPException(status_code=404, detail=f"no orchestration {orch!r}")
        params = RunParams(
            lang=str(b.get("lang", "ru")), content_type="", mode="drama",
            scenario=str(b.get("scenario", "")),
            # the cast is resolved to the full character cards here rather than passed
            # as names: `manual_cast` is what the pipeline reads, and a name it cannot
            # find would otherwise become a person with no face three stages later
            manual_cast=[store.characters[c] for c in b.get("cast", [])
                         if isinstance(c, str) and c in store.characters],
            orchestration=orch,
            duration_s=float(b.get("duration_s", 45.0)),
            parts=int(b.get("parts", 1)),
            count=int(b.get("count", 1)),
            dry_run=bool(b.get("dry_run", True)),
            breakpoints=[x for x in b.get("breakpoints", []) if isinstance(x, str)],
            duration_tol_s=float(b.get("duration_tol_s", 0.0)),
            parts_iterative=bool(b.get("parts_iterative", True)),
            clip_seconds=float(b.get("clip_seconds", 0.0)),
            **_common(b),
        )
        return sup.start(params, title=str(b.get("title", "")) or _t("web.mode.drama")).as_dict()

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
            return {"video": i, "stage": stage, "rows": [
                {"label": r.label, "value": r.value, "src": r.src, "info": r.info,
                 "readonly": r.readonly, "field": r.field, "kind": r.kind,
                 "options": r.options} for r in doc.rows]}
        return {"video": -1, "stage": "", "rows": []}

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
# The montage effects, as {key: what it does}. Read off the filter catalogue rather
# than listed here, so an effect added there appears in the form on its own.
FILTER_HELP = {e.key: e.note for e in FILTER_CATALOGUE}

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
    uvicorn.run(create_app(store), host=host, port=cfg.port, log_level="warning")
