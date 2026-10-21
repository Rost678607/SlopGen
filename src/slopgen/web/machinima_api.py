"""The browser's side of machinima: the block editor in the montage room, and the
Minecraft tab of a world.

A block's direction is edited as a form over its file (see `static/machinima.js`) and
written back whole; validating it needs the world's markup, which comes from the game
when it has the world open and from the save otherwise (`minecraft.markup`). Playing
and filming take minutes and need the game, so they run in a thread of their own and
are polled; the job is read fresh from the checkpoint when they are done and only the
block's own fields are written back, so an edit made in the room meanwhile is kept.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import Cookie, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from ..config.loader import write_fandom
from ..config.models import FandomMinecraft
from ..minecraft import block as mc_block
from ..minecraft import library, paths
from ..minecraft.anchors import Clock
from ..minecraft.client import Game, GameError, GameUnavailable
from ..minecraft.direction import BEHAVIOURS, SIDES, TIMES, VERBS, WEATHERS, Direction, validate
from ..minecraft.easing import EASES
from ..minecraft.markup import game_on, load_registry, save_registry
from ..minecraft.registry import Area, Camera, Line, Point, Registry
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.context import AppContext
from ..pipeline.job import MachinimaBlock

log = logging.getLogger(__name__)

# (run_id, video, block) -> {"state": "running"|"done"|"error", "what", "message", "since"}
WORK: dict[tuple[str, int, str], dict] = {}
_LOCK = threading.Lock()


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def _mc_of(store, fandom: str) -> FandomMinecraft | None:
    f = store.fandoms.get(fandom)
    mc = getattr(f, "minecraft", None)
    return mc if mc is not None and mc.world else None


def vocab(reg: Registry, lib, file_url) -> dict:
    """Everything the editor's dropdowns offer — the markup and the library — so a
    direction that names something that does not exist cannot be built from them."""
    places = []
    for p in reg.places:
        els = reg.elements(p)
        places.append({
            "name": p.name, "description": p.description,
            "shot": file_url(p.screenshot) if p.screenshot else "",
            "points": [{"name": e.name, "roles": e.roles, "description": e.description}
                       for e in els if isinstance(e, Point)],
            "areas": [{"name": e.name, "roles": e.roles, "description": e.description}
                      for e in els if isinstance(e, Area)],
            "lines": [{"name": e.name, "roles": e.roles, "description": e.description}
                      for e in els if isinstance(e, Line)],
            "cameras": [{"name": e.name, "kind": e.kind, "description": e.description,
                         "shot": file_url(e.screenshot) if e.screenshot else ""}
                        for e in els if isinstance(e, Camera)],
        })
    return {
        "places": places,
        "characters": [{"name": c.name, "faces": [{"name": v.name, "body": v.body, "description": v.description}
                                                   for v in c.variants]} for c in reg.characters],
        "props": [{"name": p.name, "description": p.description} for p in reg.props],
        "gestures": [{"name": n, "description": g.description, "loop": g.loop} for n, g in lib.gestures.items()],
        "poses": [{"name": n, "description": p.description} for n, p in lib.poses.items()],
        "verbs": {k: list(v) for k, v in VERBS.items()},
        "behaviours": list(BEHAVIOURS), "sides": list(SIDES), "times": list(TIMES),
        "weathers": list(WEATHERS), "eases": list(EASES),
    }


def mount_runs(app, *, store, guard, run_or_404, open_job, save, doc) -> None:
    """The block editor's routes, hung beside the montage's and sharing its way of
    opening a parked job and writing it back."""

    def mc_for(run) -> FandomMinecraft:
        mc = _mc_of(store, run.params.fandom)
        if mc is None:
            raise HTTPException(status_code=409, detail="this world is not built in Minecraft — set it in the world's Minecraft tab")
        return mc

    def block_or_404(job, bid: str) -> MachinimaBlock:
        b = mc_block.by_id(job, bid)
        if b is None:
            raise HTTPException(status_code=404, detail=f"no block {bid}")
        return b

    def block_json(run, job, b: MachinimaBlock, clock: Clock, video: int) -> dict:
        d = mc_block.load_direction(job, b)
        ws = clock.span(b.start, b.start + b.duration)

        def url(kind: str) -> str:
            rel = b.final if kind == "final" else b.draft
            if rel and (Path(job.workdir) / rel).is_file():
                return f"/api/runs/{run.id}/machinima/{b.id}/clip?video={video}&kind={kind}&v={int((Path(job.workdir) / rel).stat().st_mtime)}"
            return ""

        work = WORK.get((run.id, video, b.id))
        return {
            "id": b.id, "start": b.start, "duration": b.duration, "place": b.place, "why": b.why,
            "issues": b.issues, "stale": b.stale, "failed": b.failed, "pinned": b.pinned,
            "direction": d.model_dump(mode="json", by_alias=True) if d else None,
            "words": [{"k": k, "scene": w.scene, "i": w.i, "text": w.text, "t": round(w.start - b.start, 2)}
                      for k, w in enumerate(ws)],
            "said": " ".join(w.text for w in ws),
            "draft": url("draft"), "final": url("final"),
            "work": work,
        }

    def registry_and_lib(run):
        mc = mc_for(run)
        reg, src = load_registry(mc)
        lib = library.load(paths.save_dir(mc))
        return mc, reg, lib, src

    @app.get("/api/runs/{run_id}/machinima")
    async def read_blocks(run_id: str, video: int = 0, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        mc, reg, lib, src = await run_in_threadpool(registry_and_lib, run)
        clock = Clock.of(job.scenes)
        world = run.params.fandom

        def file_url(rel: str) -> str:
            return f"/api/worlds/{quote(world)}/minecraft/file?path={quote(rel)}"

        return {"blocks": [block_json(run, job, b, clock, i) for b in job.machinima],
                "vocab": vocab(reg, lib, file_url), "markup_from": src,
                "game": await run_in_threadpool(game_on, mc)}

    @app.post("/api/runs/{run_id}/machinima")
    async def new_block(run_id: str, request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Make a block over shots the operator chose, as they cast a card."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await _body(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        lo, hi = int(b.get("from", -1)), int(b.get("to", b.get("from", -1)))
        if not (0 <= lo <= hi < len(job.frame_shots)):
            raise HTTPException(status_code=400, detail="pick the shots the block covers")
        made = mc_block.claim(job, [{"shots": list(range(lo + 1, hi + 2)), "why": str(b.get("why", ""))}])
        if not made:
            raise HTTPException(status_code=409, detail="those shots are already played in a block")
        for m in made:
            m.pinned = True
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.delete("/api/runs/{run_id}/machinima/{bid}")
    async def drop_block(run_id: str, bid: str, video: int = 0, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        block_or_404(job, bid)
        job.machinima = [x for x in job.machinima if x.id != bid]
        for s in job.frame_shots:
            if s.block == bid:
                s.block = ""
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.put("/api/runs/{run_id}/machinima/{bid}")
    async def save_direction(run_id: str, bid: str, request: Request,
                             slopgen: str | None = Cookie(default=None)) -> dict:
        """Write the direction back whole, and say what is wrong with it. A direction
        with errors is still saved: it is a draft being worked on, and the errors are
        shown beside it — only playing it is refused."""
        guard(slopgen)
        run = run_or_404(run_id)
        body = await _body(request)
        video = int(body.get("video", 0))
        cp, i, job = open_job(run, video)
        b = block_or_404(job, bid)
        try:
            d = Direction.model_validate(body.get("direction") or {})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"not a direction: {e}"[:600])
        mc, reg, lib, _ = await run_in_threadpool(registry_and_lib, run)
        clock = Clock.of(job.scenes)
        words = {(w.scene, w.i) for w in clock.span(b.start, b.start + b.duration)}
        errors = validate(d, reg, words, set(lib.gestures), set(lib.poses))
        mc_block.save_direction(job, b, d)
        b.pinned = True
        b.failed = False
        b.issues = errors
        save(cp, i, job)
        return {"errors": errors, "block": block_json(run, job, b, clock, i)}

    def start_work(run, video: int, bid: str, what: str, fn) -> dict:
        key = (run.id, video, bid)
        with _LOCK:
            cur = WORK.get(key)
            if cur and cur["state"] == "running":
                raise HTTPException(status_code=409, detail="this block is already being worked on")
            if any(k[0] == run.id and v["state"] == "running" for k, v in WORK.items()):
                raise HTTPException(status_code=409, detail="another block of this run is in the game — one take at a time")
            WORK[key] = {"state": "running", "what": what, "message": "", "since": time.time()}

        def go():
            try:
                fn()
                WORK[key].update(state="done", message="")
            except (GameUnavailable, mc_block.MachinimaWaiting) as e:
                WORK[key].update(state="error", message=str(e))
            except Exception as e:  # shown in the editor; the log keeps the trace
                log.exception("machinima %s %s", what, bid)
                WORK[key].update(state="error", message=str(e))

        threading.Thread(target=go, name=f"machinima-{what}", daemon=True).start()
        return WORK[key]

    def merge_back(run, video: int, bid: str, job_after) -> None:
        """Write a worked-on block back into the job AS IT IS NOW on disk, touching
        nothing else of it."""
        cp = Checkpoint.load(run.run_dir)
        fresh = cp.load_job(video)
        done = mc_block.by_id(job_after, bid)
        target = mc_block.by_id(fresh, bid)
        if done is None or target is None:
            return
        for field in ("direction", "draft", "final", "timing", "stale", "issues", "failed"):
            setattr(target, field, getattr(done, field))
        if done.failed:
            for s in fresh.frame_shots:
                if s.block == bid:
                    s.block = ""
        save(cp, video, fresh)

    @app.post("/api/runs/{run_id}/machinima/{bid}/shoot")
    async def shoot(run_id: str, bid: str, request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Play and film one block: a draft (no shaders, half size) or the final."""
        guard(slopgen)
        run = run_or_404(run_id)
        body = await _body(request)
        video = int(body.get("video", 0))
        final = bool(body.get("final", False))
        cp, i, job = open_job(run, video)
        b = block_or_404(job, bid)
        if mc_block.load_direction(job, b) is None:
            raise HTTPException(status_code=409, detail="the block has no direction yet — write one or ask the director")

        def work():
            cp2 = Checkpoint.load(run.run_dir)
            job2 = cp2.load_job(video)
            ctx = AppContext(store=store, params=cp2.params)
            mc_block.shoot(job2, ctx, final=final, only={bid}, force=True)
            merge_back(run, video, bid, job2)
            b2 = mc_block.by_id(job2, bid)
            if b2 is not None and mc_block.clip(job2, b2) is None:
                raise GameError("; ".join(b2.issues[:6]) or "no clip came out of the game")

        return start_work(run, video, bid, "final" if final else "draft", work)

    @app.post("/api/runs/{run_id}/machinima/{bid}/direct")
    async def redirect(run_id: str, bid: str, request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        """Ask the director (again) for this block — the current direction is replaced."""
        guard(slopgen)
        run = run_or_404(run_id)
        body = await _body(request)
        video = int(body.get("video", 0))
        open_job(run, video)

        def work():
            from ..minecraft.direct import direct_blocks

            cp2 = Checkpoint.load(run.run_dir)
            job2 = cp2.load_job(video)
            b2 = mc_block.by_id(job2, bid)
            if b2 is None:
                return
            b2.failed = False
            _, reg, lib, _ = registry_and_lib(run)
            ctx = AppContext(store=store, params=cp2.params)
            direct_blocks(job2, [b2], reg, lib, Clock.of(job2.scenes), ctx)
            merge_back(run, video, bid, job2)

        return start_work(run, video, bid, "direct", work)

    @app.get("/api/runs/{run_id}/machinima/{bid}/work")
    async def work_state(run_id: str, bid: str, video: int = 0, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        return WORK.get((run_id, video, bid)) or {"state": "idle"}

    @app.get("/api/runs/{run_id}/machinima/{bid}/clip")
    async def block_clip(run_id: str, bid: str, video: int = 0, kind: str = "final",
                         slopgen: str | None = Cookie(default=None)) -> FileResponse:
        guard(slopgen)
        run = run_or_404(run_id)
        cp = Checkpoint.load(run.run_dir)
        job = cp.load_job(video)
        b = mc_block.by_id(job, bid) if job else None
        rel = (b.final if kind == "final" else b.draft) if b else ""
        p = Path(job.workdir) / rel if rel else None
        if p is None or not p.is_file():
            raise HTTPException(status_code=404, detail="no clip yet")
        return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "no-store"})


def mount_worlds(app, *, store, guard, world_or_404) -> None:
    """A world's Minecraft tab: where it is built, whether the game is there, and its
    markup — the same markup the in-game mode edits."""

    def mc_or_409(w) -> FandomMinecraft:
        mc = getattr(w, "minecraft", None)
        if mc is None or not mc.world:
            raise HTTPException(status_code=409, detail="say first which Prism instance and world this world is built in")
        return mc

    @app.get("/api/worlds/{name}/minecraft")
    async def world_minecraft(name: str, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        mc = getattr(w, "minecraft", None)
        out = {"config": (mc or FandomMinecraft()).model_dump(), "configured": bool(mc and mc.world),
               "game": None, "registry": None, "from": "", "library": None, "save": ""}
        if not out["configured"]:
            return out
        out["save"] = str(paths.save_dir(mc))
        out["game"] = await run_in_threadpool(game_on, mc)
        reg, src = await run_in_threadpool(load_registry, mc)
        out["registry"] = reg.model_dump(mode="json")
        out["from"] = src
        lib = library.load(paths.save_dir(mc))
        out["library"] = {"poses": {n: p.description for n, p in lib.poses.items()},
                          "gestures": {n: g.description for n, g in lib.gestures.items()}}
        return out

    @app.put("/api/worlds/{name}/minecraft")
    async def set_world_minecraft(name: str, request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        w = world_or_404(name)
        b = await _body(request)
        mc = FandomMinecraft(instance=str(b.get("instance", "")).strip(), world=str(b.get("world", "")).strip(),
                             prism_dir=str(b.get("prism_dir", "")).strip())
        w.minecraft = mc if mc.world else None
        write_fandom(w)
        return {"config": mc.model_dump(), "configured": bool(mc.world),
                "exists": bool(mc.world) and paths.save_dir(mc).is_dir() if mc.instance else False}

    @app.put("/api/worlds/{name}/minecraft/registry")
    async def put_registry(name: str, request: Request, slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        mc = mc_or_409(world_or_404(name))
        b = await _body(request)
        try:
            reg = Registry.model_validate(b.get("registry") or {})
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"not a markup: {e}"[:600])
        where = await run_in_threadpool(save_registry, mc, reg)
        return {"saved_to": where}

    @app.get("/api/worlds/{name}/minecraft/file")
    async def markup_file(name: str, path: str, slopgen: str | None = Cookie(default=None)) -> FileResponse:
        guard(slopgen)
        mc = mc_or_409(world_or_404(name))
        root = (paths.save_dir(mc) / "slopgen").resolve()
        p = (root / path).resolve()
        if root not in p.parents or not p.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        return FileResponse(p, headers={"Cache-Control": "no-store"})
