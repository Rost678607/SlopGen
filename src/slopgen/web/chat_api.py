"""The chat room's routes: one conversation, edited by hand.

The shape is :mod:`.montage_api`'s and the reasons are the same — the checkpoint is
re-read on every request because it is the authority on what a parked run knows, and
an edited job is written back leaving the run exactly as parked as it was.

What is deliberately NOT here is a second way to run a stage. `POST
/api/runs/{id}/montage/stage` is named after the room that first needed it, but what
it does is run one stage of THIS run's chain by hand, on this run's job, recorded in
this run's completed list — which is as true of a chat as of a montage. So the chat
room presses that one and re-reads its own document afterwards. A copy here would be
a second answer to the one question both screens ask, and the two would drift.

The preview is this module's own, because it is the one thing the chat room needs
that no other screen has: a frame of the conversation as it stands at one message,
drawn by the very code that will draw the video (`slopgen.chat`). An approximation in
canvas would be the usual right trade for placing things — it is instant — but here
there is nothing to place: the question the operator is asking is *what will this look
like*, and only the real renderer answers it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Cookie, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..chat.draw import Canvas
from ..config import ConfigStore, PersonaConfig
from ..config.loader import write_config
from ..pipeline import chatroom
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.context import AppContext
from ..pipeline.stages import chat_render

log = logging.getLogger(__name__)


def mount(app, *, store: ConfigStore, sup, guard, run_or_404) -> None:
    """Hang the chat-room routes on the app, using its session guard and run lookup."""

    def open_job(run, video: int):
        """`(checkpoint, index, job)` for one video of a run that is not moving."""
        if run.run_dir is None:
            raise HTTPException(status_code=409, detail="this run has no folder yet")
        if run.status in ("running", "queued"):
            raise HTTPException(status_code=409,
                                detail="this run is moving — stop it before editing it")
        if run.params.mode != "chat":
            raise HTTPException(status_code=409, detail="this run is not a conversation")
        cp = Checkpoint.load(run.run_dir)
        job = cp.load_job(video)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no video {video} in this run")
        return cp, video, job

    def save(cp: Checkpoint, i: int, job) -> None:
        """Write the edited conversation back and leave the run as parked as it was.

        The completed list is re-read off the job (`montage.completed`), which is what
        makes work done in here count as a stage having run: a conversation typed in
        this room satisfies `source`, so a chain resumed afterwards walks past the
        fetch instead of replacing what was typed."""
        from ..pipeline import montage

        status = cp.status(i)
        done = montage.completed(job, cp.completed(i))
        if status == "review":
            cp.awaiting_review(job, done, cp.review_stage(i))
        elif status == "paused":
            cp.paused(job, done, "", cp.manual_msg(i))
        elif status == "done":
            raise HTTPException(
                status_code=409,
                detail="this video is already cut — re-cut it to edit it again")
        else:
            raise HTTPException(
                status_code=409,
                detail="this run is not parked — there is nothing to edit here")

    def context(cp: Checkpoint) -> AppContext:
        return AppContext(store=store, params=cp.params)

    def doc(cp: Checkpoint, i: int, job) -> dict:
        out = chatroom.read(job, context(cp))
        out["video"] = i
        out["completed"] = cp.completed(i)
        out["stages"] = [name for name, _ in _chain(cp)]
        return out

    def _chain(cp: Checkpoint):
        from ..pipeline import orchestrator

        return orchestrator.stages_for(cp.params)

    async def body_of(request: Request) -> dict:
        try:
            b = await request.json()
        except Exception:  # noqa: BLE001 — an empty body is a legitimate request here
            return {}
        return b if isinstance(b, dict) else {}

    def edited(run, b: dict):
        """Open the job for an edit and hand back everything the handler needs.

        Every write below is the same four lines — open, change, save, answer — so the
        opening half is here and the saving half is :func:`answer`, which keeps the
        actual operation one line in each route and makes a route that forgot to save
        impossible to write by accident."""
        return open_job(run, int(b.get("video", 0)))

    def answer(cp, i, job) -> dict:
        save(cp, i, job)
        return doc(cp, i, job)

    def guarded(fn):
        """Turn a `ChatError` into the 422 it is: something the operator asked for
        that the conversation cannot be, with a sentence worth showing them."""
        def go(*a, **kw):
            try:
                return fn(*a, **kw)
            except chatroom.ChatError as e:
                raise HTTPException(status_code=422, detail=str(e))
        return go

    # -- reading ------------------------------------------------------------

    @app.get("/api/runs/{run_id}/chat")
    async def read_chat(run_id: str, video: int = 0,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """The conversation, and who is in it."""
        guard(slopgen)
        cp, i, job = open_job(run_or_404(run_id), video)
        return doc(cp, i, job)

    # -- the messages -------------------------------------------------------

    @app.post("/api/runs/{run_id}/chat/message")
    async def add_message(run_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Put a new message into the conversation."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.add)(job, int(b.get("at", len(job.messages))),
                              str(b.get("persona", "")), str(b.get("text", "")))
        return answer(cp, i, job)

    @app.put("/api/runs/{run_id}/chat/message")
    async def edit_message(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Change one message: its words, who sent it, what it answers, the rest.

        One route rather than six, for the reason `chatroom.edit` is one function: the
        screen changes several of these in one panel, and a half-applied panel is the
        one outcome nothing on it would explain."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        at = int(b.get("i", -1))
        if "text" in b:
            guarded(chatroom.set_text)(job, at, str(b["text"]))
        if "reactions" in b:
            guarded(chatroom.set_reactions)(job, at, b["reactions"])
        fields = {k: b[k] for k in
                  ("persona", "reply_to", "stamp", "nick", "avatar", "score",
                   "clear_before", "pinned") if k in b}
        if fields:
            guarded(chatroom.edit)(job, at, **fields)
        return answer(cp, i, job)

    @app.delete("/api/runs/{run_id}/chat/message")
    async def drop_message(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Take one message out, and un-aim every reply that pointed at it."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.drop)(job, int(b.get("i", -1)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/move")
    async def move_message(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Drag one message to another place in the conversation."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.move)(job, int(b.get("i", -1)), int(b.get("to", 0)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/split")
    async def split_excerpt(run_id: str, request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Begin a new excerpt here, or join this one back to the last.

        The operation IS the boundary, so pressing it twice puts the boundary back
        where it was — which is what an operator trying it out expects, and what a
        separate "join" button would get wrong half the time."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.split)(job, int(b.get("i", -1)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/import")
    async def import_text(run_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Paste a block of `Ник: текст` lines in as messages."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        n = guarded(chatroom.import_lines)(job, str(b.get("text", "")),
                                           int(b.get("at", -1)))
        out = answer(cp, i, job)
        out["added"] = n
        return out

    # -- the people ---------------------------------------------------------

    @app.post("/api/runs/{run_id}/chat/rename")
    async def rename_person(run_id: str, request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Rename one participant in this conversation.

        The CARD is not touched: a card outlives this video and renaming it belongs to
        the config panel. What this fixes is an import that got somebody's name wrong
        in every line of one chat."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        n = guarded(chatroom.rename)(job, str(b.get("was", "")), str(b.get("now", "")))
        out = answer(cp, i, job)
        out["renamed"] = n
        return out

    @app.put("/api/runs/{run_id}/chat/persona")
    async def card_person(run_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Give somebody in this conversation a card, or edit the one they have.

        A card is a CONFIG — it outlives this video and the next conversation these
        people are in — so this writes `configs/personas/<name>.toml` and reloads the
        store, rather than storing a picture and a voice on the job where the next
        video would not find them."""
        guard(slopgen)
        b = await body_of(request)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        name = str(b.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=422, detail="a card needs a name")
        was = store.personas.get(name)
        card = PersonaConfig(
            name=name,
            handle=str(b.get("handle", was.handle if was else "")),
            avatar=str(b.get("avatar", was.avatar if was else "")),
            voice=str(b.get("voice", was.voice if was else "")),
            colour=str(b.get("colour", was.colour if was else "")),
            note=str(b.get("note", was.note if was else "")),
        )
        write_config("personas", name, card.model_dump(mode="json"))
        store.personas[name] = card
        return doc(cp, i, job)

    # -- what it will look like ---------------------------------------------

    @app.get("/api/runs/{run_id}/chat/preview")
    async def preview(run_id: str, video: int = 0, at: int = -1,
                      slopgen: str | None = Cookie(default=None)):
        """One frame of the conversation, as it stands when message `at` has arrived.

        Drawn by the renderer that will draw the video, head-on: the same skin, the
        same fonts, the same wallpaper, the same initials disc for whoever has no
        picture. The one thing it does not reproduce is the clock — a preview has no
        word timings to break a long message on, so every message in it is whole,
        which is also what makes it answerable before anything has been voiced."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        if not job.messages:
            raise HTTPException(status_code=404, detail="there is no conversation yet")
        ctx = context(cp)
        last = len(job.messages) - 1 if at < 0 else max(0, min(at, len(job.messages) - 1))
        out = Path(job.workdir) / "chat" / "preview.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_in_threadpool(_draw_preview, job, ctx, last, out)
        except Exception as e:  # noqa: BLE001
            log.exception("drawing the chat preview failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return FileResponse(out, media_type="image/png",
                            headers={"Cache-Control": "no-store"})


def _draw_preview(job, ctx: AppContext, last: int, out: Path) -> None:
    """Lay the conversation up to `last` and paint the frame it ends on.

    The same planner the render stage drives, stopped early (`Planner.lay_upto`), so
    the preview and the video cannot disagree about anything but the clock."""
    from ..chat.scroll import Planner

    skin = ctx.chat_skin
    cfg = ctx.chat
    v = ctx.g.video
    wallpaper = (chat_render.asset(ctx, chat_render.BACKGROUNDS_DIR, cfg.background)
                 if skin.wallpaper else None)
    planner = Planner(ctx, job, v.width, v.height,
                      people=chat_render.people_of(job, ctx), wallpaper=wallpaper,
                      top_inset=skin.header_h if cfg.header else 0)
    canvas: Canvas = planner.lay_upto(last)
    frame = canvas.band(int(max(0.0, canvas.height - v.height)), v.height)
    if cfg.header:
        bar = canvas.header_image(cfg.title or job.chat_title or "",
                                  chat_render.asset(ctx, chat_render.AVATARS_DIR,
                                                    cfg.header_avatar))
        frame.paste(bar, (0, 0), bar)
    frame.save(out)
