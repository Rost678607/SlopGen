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

from fastapi import Cookie, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from PIL import Image

from ..chat.draw import Canvas
from pydantic import ValidationError

from ..config import ChatConfig, ConfigStore, PersonaConfig
from ..config.loader import write_config
from ..pipeline import chatroom
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.job import ChatMsg, Conversation
from ..pipeline.context import AppContext
from ..chat import exports, reddit, telegram
from ..llm import chat as chat_llm
from ..pipeline.stages import chat_render, chat_source

log = logging.getLogger(__name__)


# What the room may change about the run, and how each control is drawn. Written down
# rather than derived from `ChatConfig`, for the reason `montage_api.SETTINGS` is: the
# model knows the types and nothing about which of them are worth a control, which ones
# only make sense for one skin, or what order they read in. The browser knows the six
# kinds and nothing about what any particular setting means, so adding one here is the
# whole of adding one.
SHEET: list[dict] = [
    {"head": "web.chat.set.look"},
    {"f": "skin", "kind": "select", "l": "web.f.skin", "opts": "chat_skins"},
    {"f": "header", "kind": "check", "l": "web.f.chathead"},
    {"f": "title", "kind": "text", "l": "web.f.chatname"},
    {"f": "header_avatar", "kind": "select", "l": "web.f.chatavatar", "opts": "avatars",
     "blank": True},
    {"f": "me", "kind": "select", "l": "web.f.chatme", "opts": "personas", "blank": True,
     "when": "telegram"},
    {"f": "background", "kind": "select", "l": "web.f.chatbg", "opts": "chat_backgrounds",
     "blank": True, "when": "telegram"},
    {"head": "web.chat.set.clock"},
    {"f": "scroll", "kind": "select", "l": "web.f.scroll", "opts": "scroll_modes",
     "opt_l": "scr."},
    {"f": "roll_s", "kind": "number", "l": "web.f.rolls", "min": 0, "max": 3, "step": 0.05},
    {"f": "swipe_s", "kind": "number", "l": "web.f.swipes", "min": 0, "max": 3, "step": 0.05},
    {"f": "gap_s", "kind": "number", "l": "web.f.gaps", "min": 0, "max": 10, "step": 0.1},
    {"f": "chunk", "kind": "number", "l": "web.f.chunk", "min": 0, "max": 400, "step": 10},
    {"f": "chunk_min", "kind": "number", "l": "web.f.chunkmin", "min": 0, "max": 800, "step": 10},
    {"head": "web.chat.set.extra"},
    {"f": "reactions", "kind": "check", "l": "web.f.chatreact"},
    {"f": "react_s", "kind": "number", "l": "web.f.reacts", "min": 0, "max": 4, "step": 0.1},
    {"f": "translate", "kind": "check", "l": "web.f.chattr"},
    {"f": "sfx", "kind": "select", "l": "web.f.sfx", "opts": "chat_sounds", "blank": True,
     "blank_l": "w.sfx.roll"},
    {"f": "sfx_volume", "kind": "number", "l": "web.f.sfxvol", "min": 0, "max": 2,
     "step": 0.05},
    {"head": "web.chat.set.frame"},
    {"f": "aspect", "kind": "select", "l": "web.f.aspect", "opts": "aspects"},
    {"f": "split", "kind": "check", "l": "web.f.split"},
    {"f": "split_clip", "kind": "select", "l": "web.f.splitclip", "opts": "chat_clips",
     "blank": True, "blank_l": "w.clip.roll"},
    {"f": "split_share", "kind": "number", "l": "web.f.splitshare", "min": 0.2,
     "max": 0.95, "step": 0.01},
    {"f": "split_change_s", "kind": "number", "l": "web.f.splitchange", "min": 0,
     "max": 120, "step": 5},
]


def mount(app, *, store: ConfigStore, sup, guard, run_or_404) -> None:
    """Hang the chat-room routes on the app, using its session guard and run lookup."""

    # The Telegram sign-in in progress, if there is one. One per server and not one
    # per run, because what is being signed into is the MACHINE's session: a second
    # flow would be a second connection to Telegram racing the first for the same
    # session file. A dict rather than a bare name so the handlers can rebind it.
    _login: dict[str, telegram.Login | None] = {"flow": None}

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
        ctx = context(cp)
        out = chatroom.read(job, ctx)
        out["video"] = i
        out["completed"] = cp.completed(i)
        out["stages"] = [name for name, _ in _chain(cp)]
        out["sheet"] = SHEET
        out["settings"] = ctx.chat.model_dump(mode="json")
        # The clock, once there is one. A conversation being built has no timings at
        # all — they do not exist until something has voiced it and drawn it — so the
        # room shows a list until then and a timeline afterwards, which is the honest
        # order and not a limitation to apologise for.
        out["clock"] = _clock(job)
        return out

    def _clock(job) -> dict:
        """Where every message lands on the finished video's clock, for the strip the
        room scrubs along. Empty until the scenes have been laid and voiced."""
        if not job.scenes:
            return {"total": 0.0, "marks": []}
        at, starts = 0.0, []
        for scene in job.scenes:
            starts.append(at)
            at += scene.duration
        marks = []
        for n, msg in enumerate(job.messages):
            if 0 <= msg.scene < len(starts):
                marks.append({"i": n, "at": starts[msg.scene]})
        return {"total": at, "marks": marks}

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
        c = int(b.get("c", 0))
        guarded(chatroom.add)(job, c, int(b.get("at", 1 << 30)),
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
        c, at = int(b.get("c", 0)), int(b.get("i", -1))
        if "text" in b:
            guarded(chatroom.set_text)(job, c, at, str(b["text"]))
        if "reactions" in b:
            guarded(chatroom.set_reactions)(job, c, at, b["reactions"])
        fields = {k: b[k] for k in
                  ("persona", "reply_to", "stamp", "nick", "avatar", "score",
                   "clear_before", "pinned") if k in b}
        if fields:
            guarded(chatroom.edit)(job, c, at, **fields)
        return answer(cp, i, job)

    @app.delete("/api/runs/{run_id}/chat/message")
    async def drop_message(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Take one message out, and un-aim every reply that pointed at it."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.drop)(job, int(b.get("c", 0)), int(b.get("i", -1)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/move")
    async def move_message(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Drag one message to another place in the conversation."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.move)(job, int(b.get("c", 0)), int(b.get("i", -1)),
                               int(b.get("to", 0)))
        return answer(cp, i, job)

    # -- the conversations ---------------------------------------------------

    @app.post("/api/runs/{run_id}/chat/conversation")
    async def add_conversation(run_id: str, request: Request,
                               slopgen: str | None = Cookie(default=None)) -> dict:
        """Put another piece of conversation into the video."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.add_conversation)(job, int(b.get("at", -1)),
                                           str(b.get("title", "")))
        return answer(cp, i, job)

    @app.put("/api/runs/{run_id}/chat/conversation")
    async def name_conversation(run_id: str, request: Request,
                                slopgen: str | None = Cookie(default=None)) -> dict:
        """Name a piece, or record where it came from."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        fields = {k: b[k] for k in ("title", "source") if k in b}
        guarded(chatroom.set_conversation)(job, int(b.get("c", 0)), **fields)
        return answer(cp, i, job)

    @app.delete("/api/runs/{run_id}/chat/conversation")
    async def drop_conversation(run_id: str, request: Request,
                                slopgen: str | None = Cookie(default=None)) -> dict:
        """Take one piece out, with everything said in it."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.drop_conversation)(job, int(b.get("c", 0)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/conversation/move")
    async def move_conversation(run_id: str, request: Request,
                                slopgen: str | None = Cookie(default=None)) -> dict:
        """Show this piece earlier or later in the video."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.move_conversation)(job, int(b.get("c", 0)), int(b.get("to", 0)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/split")
    async def split_conversation(run_id: str, request: Request,
                                 slopgen: str | None = Cookie(default=None)) -> dict:
        """Cut this conversation in two here; the tail becomes the next piece."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.split)(job, int(b.get("c", 0)), int(b.get("i", -1)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/join")
    async def join_conversation(run_id: str, request: Request,
                                slopgen: str | None = Cookie(default=None)) -> dict:
        """Fold this piece back into the one before it — the undo of a split."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        guarded(chatroom.join)(job, int(b.get("c", 0)))
        return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/import")
    async def import_text(run_id: str, request: Request,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """Paste a block of `Ник: текст` lines in as messages."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        c = int(b.get("c", 0))
        if not job.conversations:
            chatroom.add_conversation(job)
            c = 0
        n = guarded(chatroom.import_lines)(job, c, str(b.get("text", "")),
                                           int(b.get("at", -1)))
        out = answer(cp, i, job)
        out["added"] = n
        return out

    # -- the exports base ----------------------------------------------------

    @app.get("/api/runs/{run_id}/chat/exports")
    async def list_exports(run_id: str, video: int = 0,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """What is in the base, and what each file turns out to be.

        The format is sniffed rather than taken from the name: `result.json` is what
        three different programs call their export, and an operator who renamed one is
        not wrong to expect it to still work. A file that is not an export at all is
        listed with no format rather than hidden — it is in the folder, and being told
        it cannot be read is more use than it quietly not appearing."""
        guard(slopgen)
        cp, i, job = open_job(run_or_404(run_id), video)
        root = chat_source.exports_root(context(cp))
        out = []
        for at in sorted(root.rglob("*") if root.is_dir() else []):
            # dotfiles are never exports: the `.gitkeep` that holds the folder in git
            # is the one that would otherwise be listed as unreadable on every machine
            if not at.is_file() or at.name.startswith("."):
                continue
            try:
                kind = exports.sniff(at.read_bytes()[:1 << 20])
            except OSError:
                continue
            out.append({"name": at.relative_to(root).as_posix(),
                        "format": kind or "", "size": at.stat().st_size})
        return {"exports": out}

    @app.post("/api/runs/{run_id}/chat/exports")
    async def add_export(run_id: str, file: UploadFile,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Put one export into the base.

        It is kept rather than read and thrown away, because the same thread is cut
        three different ways over a month and re-uploading it each time is the
        operator's evening. Refused if nothing in it can be read: a base of files that
        turn out not to be exports is a base you have to remember things about."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, _i, _job = open_job(run, 0)
        name = Path(str(file.filename or "export")).name
        if not name or name.startswith("."):
            raise HTTPException(status_code=422, detail="that file has no usable name")
        root = chat_source.exports_root(context(cp))
        root.mkdir(parents=True, exist_ok=True)
        at = root / name
        body = await file.read()
        if exports.sniff(body[:1 << 20]) is None:
            raise HTTPException(
                status_code=422,
                detail=f"{name} is not an export this reads: expected Telegram's JSON "
                       "or HTML, a DiscordChatExporter JSON, or a reddit thread's .json")
        at.write_bytes(body)
        return {"name": name, "size": len(body),
                "format": exports.sniff(body[:1 << 20]) or ""}

    @app.get("/api/runs/{run_id}/chat/exports/read")
    async def read_export(run_id: str, name: str, video: int = 0,
                          slopgen: str | None = Cookie(default=None)) -> dict:
        """What one export holds, without putting any of it into the video yet.

        A whole-account Telegram export is four hundred chats and two of them are
        worth a video, so what comes back is the list with a line of each — enough to
        recognise one, and not the three megabytes of it."""
        guard(slopgen)
        cp, _i, _job = open_job(run_or_404(run_id), video)
        try:
            pieces = exports.read(chat_source.pick(context(cp), name))
        except (exports.ExportError, ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"name": name, "pieces": [
            {"p": n, "title": p.title, "lines": len(p.lines),
             "who": sorted({ln.who for ln in p.lines if ln.who})[:6],
             "first": p.lines[0].text[:140] if p.lines else ""}
            for n, p in enumerate(pieces)]}

    # -- signing in to Telegram, which is three steps and cannot be fewer ----

    @app.get("/api/chat/telegram")
    async def telegram_status(slopgen: str | None = Cookie(default=None)) -> dict:
        """Whether this machine can read Telegram, and as whom."""
        guard(slopgen)
        out = await telegram.status(store.global_cfg.paths.state)
        out["step"] = _login["flow"].step if _login["flow"] else ""
        return out

    @app.post("/api/chat/telegram/login")
    async def telegram_login(request: Request,
                             slopgen: str | None = Cookie(default=None)) -> dict:
        """Ask Telegram to send a code to a number, and hold the sign-in open.

        Held because it has to be: Telegram answers the number with a hash the code
        must be sent back WITH, good only on the connection it was issued on. So the
        client stays open between these three requests, and a flow somebody walked
        away from is closed when the next one starts rather than left holding a
        connection to Telegram forever."""
        guard(slopgen)
        b = await body_of(request)
        if _login["flow"] is not None:
            await _login["flow"].close()
            _login["flow"] = None
        try:
            _login["flow"] = await telegram.begin(store.global_cfg.paths.state,
                                                  str(b.get("phone", "")))
        except telegram.TelegramError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return {"step": _login["flow"].step}

    @app.post("/api/chat/telegram/code")
    async def telegram_code(request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Hand over the code Telegram sent. Answers `done`, or `password`."""
        guard(slopgen)
        b = await body_of(request)
        flow = _login["flow"]
        if flow is None:
            raise HTTPException(status_code=409, detail="there is no sign-in waiting")
        try:
            await telegram.with_code(flow, str(b.get("code", "")))
        except telegram.TelegramError as e:
            if flow.step == "phone":
                _login["flow"] = None
            raise HTTPException(status_code=422, detail=str(e))
        if flow.step == "done":
            _login["flow"] = None
        return {"step": flow.step}

    @app.post("/api/chat/telegram/password")
    async def telegram_password(request: Request,
                                slopgen: str | None = Cookie(default=None)) -> dict:
        """Hand over the two-step password, for an account that has one."""
        guard(slopgen)
        b = await body_of(request)
        flow = _login["flow"]
        if flow is None:
            raise HTTPException(status_code=409, detail="there is no sign-in waiting")
        try:
            await telegram.with_password(flow, str(b.get("password", "")))
        except telegram.TelegramError as e:
            raise HTTPException(status_code=422, detail=str(e))
        _login["flow"] = None
        return {"step": flow.step}

    @app.post("/api/chat/telegram/logout")
    async def telegram_logout(slopgen: str | None = Cookie(default=None)) -> dict:
        """Log the account out, and take the session file with it."""
        guard(slopgen)
        if _login["flow"] is not None:
            await _login["flow"].close()
            _login["flow"] = None
        await telegram.sign_out(store.global_cfg.paths.state)
        return await telegram.status(store.global_cfg.paths.state)

    # -- looking at what a source has ---------------------------------------

    @app.get("/api/chat/browse")
    async def browse(source: str, where: str = "", sort: str = "hot",
                     slopgen: str | None = Cookie(default=None)) -> dict:
        """What a source has, as something to choose from — never the whole of it.

        Browsing is deciding what is worth reading, so neither of these brings back a
        single message: a hundred reddit threads with their comment trees attached is
        several megabytes to answer a question about titles, and a hundred Telegram
        chats with their histories is several minutes of requests to answer a question
        about names."""
        guard(slopgen)
        try:
            if source == "reddit":
                return {"source": source, "rows": [
                    {"id": r["url"], "title": r["title"],
                     "note": f"↑{r['score']} · {r['comments']}",
                     "text": r["text"][:200]}
                    for r in reddit.listing(where, sort)]}
            if source == "telegram":
                return {"source": source, "rows": [
                    {"id": str(d["id"]), "title": d["title"], "note": d["kind"],
                     "text": ""}
                    for d in await telegram.dialogs(store.global_cfg.paths.state)]}
        except (reddit.RedditError, telegram.TelegramError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        raise HTTPException(status_code=404, detail=f"nothing browses {source!r}")

    @app.get("/api/runs/{run_id}/chat/peek")
    async def peek(run_id: str, source: str, where: str = "", piece: int = 0,
                   before: int = 0, video: int = 0,
                   slopgen: str | None = Cookie(default=None)) -> dict:
        """What is actually in there, before any of it is in the video.

        This is the step that was missing, and the one the operator noticed was
        missing: taking a chat used to mean taking its last hundred-odd messages
        sight unseen, which for a chat of ten thousand is an arbitrary stretch of
        somebody's year. A chat is not a thing you take; a stretch of it is. So the
        messages come back here to be looked at and chosen from, and `before` walks
        backwards through them a window at a time — a message id for Telegram, which
        is the only source with more than one window in it."""
        guard(slopgen)
        cp, _i, _job = open_job(run_or_404(run_id), video)
        ctx = context(cp)
        try:
            if source == "telegram":
                got, oldest, more = await telegram.window(
                    store.global_cfg.paths.state, where, before=before,
                    # the pictures land in the avatar base, so they are the same files
                    # a card points at and the same ones next week's video reuses
                    photos=store.global_cfg.paths.assets / chat_render.AVATARS_DIR)
                pieces, cursor = [got], oldest
            elif source == "reddit":
                pieces, cursor, more = await run_in_threadpool(reddit.thread, where), 0, False
            elif source == "export":
                pieces = exports.read(chat_source.pick(ctx, where))
                cursor, more = 0, False
            else:
                raise HTTPException(status_code=404, detail=f"nothing reads {source!r}")
        except (reddit.RedditError, telegram.TelegramError, exports.ExportError,
                ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        at = max(0, min(int(piece), len(pieces) - 1)) if pieces else 0
        got = pieces[at] if pieces else None
        return {
            "title": got.title if got else "",
            "source": source,
            "where": where,
            # a multi-chat export has more than one; everything else has exactly one
            "pieces": [{"p": n, "title": p.title, "lines": len(p.lines)}
                       for n, p in enumerate(pieces)],
            "piece": at,
            "before": cursor,
            "more": bool(more),
            "lines": [
                {"i": n, "who": ln.who, "text": ln.text, "stamp": ln.stamp,
                 "reply_to": ln.reply_to, "score": ln.score, "avatar": ln.avatar,
                 "reactions": [[e, c] for e, c in ln.reactions]}
                for n, ln in enumerate(got.lines)] if got else [],
        }

    @app.post("/api/runs/{run_id}/chat/take")
    async def take_lines(run_id: str, request: Request,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Put the messages somebody actually chose into the video, and only those.

        The lines come back from the browser rather than being re-fetched, because
        re-fetching is the one way this could go wrong: a window of a live chat is not
        the same window a minute later, and `take the 4th, 7th and 9th of what I was
        looking at` has to mean what was on the screen. They are the operator's own
        bytes either way — the room lets them rewrite every one.

        Replies are remapped onto what was kept, so a reply whose target was left
        behind becomes no reply rather than pointing at whichever line slid into that
        number — the quiet lie this mode refuses everywhere else."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        rows = [r for r in (b.get("lines") or []) if isinstance(r, dict)]
        if not rows:
            raise HTTPException(status_code=422, detail="nothing was chosen")
        moved = {int(r.get("i", -1)): n for n, r in enumerate(rows)}
        conv = Conversation(title=str(b.get("title", "")).strip(),
                            source=str(b.get("source", "")).strip())
        for n, r in enumerate(rows):
            was = int(r.get("reply_to", -1))
            conv.messages.append(ChatMsg(
                persona=str(r.get("who", "")).strip(),
                text=str(r.get("text", "")),
                stamp=str(r.get("stamp", "")),
                # The picture rides on the MESSAGE and not on a card, because carding
                # somebody is the operator's decision and an import must not make it
                # for them. When they do card the person, the room offers this as the
                # picture to put on it.
                avatar=str(r.get("avatar", "")),
                score=int(r.get("score", 0) or 0),
                reply_to=moved.get(was, -1) if was >= 0 and moved.get(was, n) != n else -1,
                reactions=[(str(e), max(1, int(c)))
                           for e, c in (r.get("reactions") or []) if str(e).strip()],
            ))
        job.conversations.append(conv)
        out = answer(cp, i, job)
        out["added"] = 1
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

    @app.put("/api/runs/{run_id}/chat/settings")
    async def set_settings(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """Change how this run draws its conversations, with the frame in front of you.

        Onto the run's own `manual_chat` rather than onto the preset it may have been
        started from: a preset is a thing many videos share, and changing one of them
        from inside one video would change the others behind their operators' backs. So
        the first edit here copies the preset into the run and edits the copy, and the
        run stops following that preset — which is what "this video's settings" means
        and what the sheet says over them."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        known = {row["f"] for row in SHEET if "f" in row}
        fields = {k: v for k, v in b.items() if k in known}
        if fields:
            base = context(cp).chat.model_dump(mode="json")
            base.update(fields)
            try:
                fresh = ChatConfig.model_validate(base)
            except ValidationError as e:
                raise HTTPException(status_code=422,
                                    detail=f"chat settings: {e.errors()[0]['msg']}")
            run.params.manual_chat = fresh
            cp.data["params"]["manual_chat"] = fresh.model_dump(mode="json")
            cp.save()
        return doc(cp, i, job)

    @app.get("/api/avatar")
    async def avatar(name: str, slopgen: str | None = Cookie(default=None)):
        """One picture out of the avatar base, so the room can show what it is offering.

        Inside the folder and nowhere else: the name comes off a form, so it is
        resolved against the base and checked to still be under it."""
        guard(slopgen)
        root = (store.global_cfg.paths.assets / chat_render.AVATARS_DIR).resolve()
        at = (root / name).resolve()
        if not str(at).startswith(str(root)) or not at.is_file():
            raise HTTPException(status_code=404, detail="no such picture")
        return FileResponse(at, headers={"Cache-Control": "max-age=300"})

    # -- what it will look like ---------------------------------------------

    @app.get("/api/runs/{run_id}/chat/preview")
    async def preview(run_id: str, video: int = 0, at: int = -1, t: float = -1.0,
                      slopgen: str | None = Cookie(default=None)):
        """One frame, asked for in whichever of the two ways there is an answer to.

        `t` is a MOMENT of the finished video, and it is the honest preview — the
        state that is up then, cropped where its window has travelled to by then, with
        its own header bar on it. It needs the drawing to have happened, so it is what
        the room offers once `render` has run and the timeline exists.

        `at` is a MESSAGE, and it is what there is before that. A conversation being
        built has no timings at all — they do not exist until something has voiced it
        — so this lays the conversation out as far as that message and paints the
        screen it ends on. Every message in it is whole, because there is nothing yet
        to break a long one on.

        Both go through the code that will draw the video: a preview computed some
        cheaper second way is a preview that disagrees with the render exactly where
        it matters."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        if not job.messages:
            raise HTTPException(status_code=404, detail="there is no conversation yet")
        ctx = context(cp)
        out = Path(job.workdir) / "chat" / "preview.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            if t >= 0 and job.chat_states:
                await run_in_threadpool(_frame_at, job, ctx, float(t), out)
            else:
                last = (len(job.messages) - 1 if at < 0
                        else max(0, min(at, len(job.messages) - 1)))
                await run_in_threadpool(_draw_preview, job, ctx, last, out)
        except Exception as e:  # noqa: BLE001
            log.exception("drawing the chat preview failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return FileResponse(out, media_type="image/png",
                            headers={"Cache-Control": "no-store"})


def _frame_at(job, ctx: AppContext, when: float, out: Path) -> None:
    """The frame the finished video shows at `when`, out of the states already drawn.

    The same arithmetic `ffmpeg.make_chat_part` renders with, done once in Pillow: find
    the state that is up, interpolate its window along the ramp, crop, lay the bar on
    top. Not a second opinion about what the video looks like — the same numbers,
    evaluated at one instant — which is why scrubbing is honest and costs no encoder."""
    cfg = ctx.chat
    v = ctx.g.video
    states = job.chat_states
    at = max(0.0, float(when))
    st = states[0]
    for s in states:
        if s.start <= at + 1e-6:
            st = s
        else:
            break
    img = Image.open(st.path).convert("RGB")
    top = chat_render.chat_height(ctx)
    span = max(cfg.swipe_s if st.swipe else cfg.roll_s, 1e-3)
    k = min(max((at - st.start) / span, 0.0), 1.0)
    y = st.from_y + (st.to_y - st.from_y) * k
    x = (v.width * k) if st.swipe else 0.0
    x = min(max(x, 0), max(img.width - v.width, 0))
    y = min(max(y, 0), max(img.height - top, 0))
    frame = img.crop((int(x), int(y), int(x) + v.width, int(y) + top))
    # A swipe already carries both bars inside the picture; everything else wears its
    # own as an overlay, exactly as the assembler lays it (see `chat_render`).
    if st.overlay and not st.swipe and Path(st.overlay).is_file():
        with Image.open(st.overlay) as bar:
            bar = bar.convert("RGBA")
            frame.paste(bar, (0, 0), bar)
    _framed(frame, v, top).save(out)


def _framed(chat, v, top: int):
    """The chat half on a frame of the real shape, when something else fills the rest.

    Black rather than a sample of the loop playing under it: the preview is about the
    conversation, and the honest thing to show is how much of the screen it will have
    — not a guess at which second of somebody's gameplay will be under it."""
    if top >= v.height:
        return chat
    out = Image.new("RGB", (v.width, v.height), "#000000")
    out.paste(chat, (0, 0))
    return out


def _draw_preview(job, ctx: AppContext, last: int, out: Path) -> None:
    """Lay the conversation up to `last` and paint the frame it ends on.

    The same planner the render stage drives, stopped early (`Planner.lay_upto`), so
    the preview and the video cannot disagree about anything but the clock."""
    from ..chat.scroll import Planner

    skin = ctx.chat_skin
    cfg = ctx.chat
    v = ctx.g.video
    top = chat_render.chat_height(ctx)
    wallpaper = (chat_render.asset(ctx, chat_render.BACKGROUNDS_DIR, cfg.background)
                 if skin.wallpaper else None)
    planner = Planner(ctx, job, v.width, top,
                      people=chat_render.people_of(job, ctx), wallpaper=wallpaper,
                      top_inset=skin.header_h if cfg.header else 0)
    canvas: Canvas = planner.lay_upto(last)
    frame = canvas.band(int(max(0.0, canvas.height - top)), top)
    if cfg.header:
        conv = next((c for c in job.conversations
                     if any(m is job.messages[last] for m in c.messages)), None)
        bar = canvas.header_image((conv.title if conv else "") or cfg.title
                                  or job.chat_title or "",
                                  chat_render.asset(ctx, chat_render.AVATARS_DIR,
                                                    cfg.header_avatar))
        frame.paste(bar, (0, 0), bar)
    _framed(frame, v, top).save(out)
