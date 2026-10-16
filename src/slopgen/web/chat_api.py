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

import asyncio
import io
import logging
from pathlib import Path

from fastapi import Cookie, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from PIL import Image

from ..chat.draw import Canvas
from pydantic import ValidationError

from ..config import ChatConfig, ConfigStore, PersonaConfig
from ..config.loader import delete_config, write_config
from ..pipeline import chatroom
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.job import ChatMedia, ChatMsg, Conversation
from ..pipeline.context import AppContext
from ..chat import exports, media as chat_media, reddit, telegram
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
    # Not drawn in the sheet — `"hide": True` — because it is answered on the person:
    # every card in the cast carries `это я`, which is where somebody looking for
    # "whose account is this" actually looks. It stays in the sheet's list all the
    # same, because that list is also what the settings route will accept, and one
    # place deciding both is what keeps them from drifting apart.
    {"f": "me", "kind": "select", "l": "web.f.chatme", "opts": "cast", "blank": True,
     "hide": True,
     "when": "telegram"},
    {"f": "background", "kind": "select", "l": "web.f.chatbg", "opts": "chat_backgrounds",
     "blank": True, "when": "telegram"},
    {"head": "web.chat.set.clock"},
    {"f": "scroll", "kind": "select", "l": "web.f.scroll", "opts": "scroll_modes",
     "opt_l": "scr."},
    {"f": "roll_s", "kind": "number", "l": "web.f.rolls", "min": 0, "max": 3, "step": 0.05},
    {"f": "swipe_s", "kind": "number", "l": "web.f.swipes", "min": 0, "max": 3, "step": 0.05},
    {"f": "gap_s", "kind": "number", "l": "web.f.gaps", "min": 0, "max": 10, "step": 0.1},
    {"f": "media_s", "kind": "number", "l": "web.f.medias", "min": 0, "max": 10, "step": 0.1},
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
    # the split exists in a vertical frame only (see `chat_render.split_on`), so its
    # rows are drawn only while the aspect says so — `aspect` is to these what `when`
    # is to the rows of one skin
    {"f": "split", "kind": "check", "l": "web.f.split", "aspect": "9:16"},
    {"f": "split_clip", "kind": "select", "l": "web.f.splitclip", "opts": "chat_clips",
     "blank": True, "blank_l": "w.clip.roll", "aspect": "9:16"},
    {"f": "split_change_s", "kind": "number", "l": "web.f.splitchange", "min": 0,
     "max": 120, "step": 5, "aspect": "9:16"},
    # The subtitles. Not the chat preset's but the RUN's (`RunParams`), as on the start
    # form — `"run": True` says so, and the route writes them there. Here as well as
    # there because the frame they are burned onto is on this screen, and a setting of
    # the picture that could only be reached by starting the run again was a setting
    # that, from here, did not exist.
    {"head": "web.card.subs"},
    {"note": "web.chat.subs.note"},
    {"f": "subtitle_style", "kind": "select", "l": "web.f.style", "opts": "subtitle_styles",
     "opt_l": "w.", "blank": True, "blank_l": "web.chat.subs.default", "run": True},
    {"f": "clean_subtitles", "kind": "check", "l": "web.f.clean", "run": True},
]

# the rows of SHEET that are the run's and not the chat preset's
RUN_FIELDS = {row["f"] for row in SHEET if row.get("run")}


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

    # -- what a message carries, between seeing it and taking it --------------
    #
    # A message being looked at in the picker carries a REFERENCE to its file and not
    # the file: an export's chat is a year of somebody's pictures, and copying all of
    # them into the media base to show nine would be the slowest way to open a list.
    # `export:<path inside the exports base>` is a file still sitting in an unpacked
    # export; `media:<name>` is one already in the base (a live read downloads there
    # directly). Taking the message is what copies it in, and both kinds are resolved
    # against their own folder and nowhere else — the reference comes back off the
    # browser, and a message is not a way to read `../../.env`.

    def media_base() -> Path:
        return chat_media.root(store.global_cfg.paths.assets)

    def ref_of(m) -> str:
        if m is None:
            return ""
        if m.file:
            return f"media:{m.file}"
        root = (store.global_cfg.paths.assets / chat_source.EXPORTS_DIR).resolve()
        try:
            return f"export:{Path(m.path).resolve().relative_to(root).as_posix()}"
        except ValueError:
            return ""

    def ref_path(ref: str) -> Path | None:
        kind, _, rest = str(ref or "").partition(":")
        if kind == "media":
            return chat_media.resolve(media_base(), rest)
        if kind == "export":
            root = (store.global_cfg.paths.assets / chat_source.EXPORTS_DIR).resolve()
            at = (root / rest).resolve()
            return at if root in at.parents and at.is_file() else None
        return None

    def media_json(m) -> dict | None:
        ref = ref_of(m)
        if m is None or not ref:
            return None
        return {"kind": m.kind, "ref": ref, "w": m.w, "h": m.h,
                "duration": m.duration, "emoji": m.emoji}

    def take_media(row) -> ChatMedia | None:
        """The media a taken line came back with, copied into the base."""
        if not isinstance(row, dict):
            return None
        src = ref_path(str(row.get("ref", "")))
        kind = str(row.get("kind", ""))
        if src is None or kind not in chat_media.KINDS:
            return None
        name = chat_media.adopt(src, media_base())
        w, h, dur, _moves = chat_media.facts(media_base() / name)
        return ChatMedia(kind=kind, file=name, w=int(row.get("w") or 0) or w,
                         h=int(row.get("h") or 0) or h,
                         duration=float(row.get("duration") or 0.0) or dur,
                         emoji=str(row.get("emoji", ""))[:16])

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
        # What each message is read with now and whether it has been read: the voice
        # the casting would give it (its own, the run's or its card's), and whether
        # its scene holds a take of what it now says. The room's voice controls show
        # the first and its re-voice button is about the second.
        from ..pipeline.stages.chat_script import voice_for
        flat = iter(job.messages)
        for conv in out["conversations"]:
            for row in conv["messages"]:
                m = next(flat)
                sc = job.scenes[m.scene] if 0 <= m.scene < len(job.scenes) else None
                row["speaks"] = voice_for(m, ctx)
                row["laid"] = sc is not None and not sc.hush
                row["take"] = bool(sc is not None and sc.audio and sc.text == m.text
                                   and Path(sc.audio).is_file())
        # the run's own rows of the sheet (see `RUN_FIELDS`)
        out["run_settings"] = {
            "subtitle_style": cp.params.subtitle_style or "",
            "clean_subtitles": bool(cp.params.clean_subtitles),
            # not a row of the sheet — it is the start form's — but the message's
            # voice select says what "as the person reads" currently means
            "chat_voice": cp.params.chat_voice or "",
        }
        # The clock, once there is one. A conversation being built has no timings at
        # all — they do not exist until something has voiced it and drawn it — so the
        # room shows a list until then and a timeline afterwards, which is the honest
        # order and not a limitation to apologise for.
        out["clock"] = _clock(job)
        # What the room PLAYS by: where every message starts and how long it is up.
        # The real clock when the scenes are laid, voiced and still the conversation
        # on the screen; otherwise the same rules guessed at (`chat_script.guess`) —
        # a conversation being typed has to be watchable too, and that is the moment
        # its pace is being decided.
        out["pace"] = _pace(job, ctx)
        # Whether there is a finished cut to WATCH. A still frame answers "what will
        # this look like"; it cannot answer "how does it move", and the reveal, the
        # roll and the swipe are most of what this mode is. So the room says when the
        # real thing exists and plays it (see `/api/runs/{id}/video`).
        out["video"] = any(p.file and Path(p.file).is_file() for p in job.parts)
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
                # who and what, so a tick can say what it is: a strip of anonymous
                # chalk marks tells you a message starts there and nothing about
                # which, which is no help when what you are looking for is a line
                marks.append({"i": n, "at": starts[msg.scene],
                              "who": msg.nick or msg.persona,
                              "text": (msg.text or "")[:60]})
        return {"total": at, "marks": marks}

    def _pace(job, ctx) -> dict:
        from ..pipeline.chat_reel import laid as is_laid

        msgs = job.messages
        starts, at = [], 0.0
        for scene in job.scenes:
            starts.append(at)
            at += scene.duration
        # Exact only while the scenes still ARE these messages (see `chat_reel.laid`).
        laid = is_laid(job)
        if laid:
            begin = [starts[m.scene] for m in msgs]
            dur = [e - b for b, e in zip(begin, begin[1:] + [at])]
        else:
            from ..pipeline.stages import chat_script

            dur = chat_script.guess(job, ctx)
            begin, t = [], 0.0
            for d in dur:
                begin.append(round(t, 3))
                t += d
        return {"exact": laid, "total": round(sum(dur), 3),
                "at": [round(b, 3) for b in begin], "dur": [round(d, 3) for d in dur]}

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
                  ("persona", "reply_to", "stamp", "day", "nick", "avatar", "voice",
                   "score", "clear_before", "pinned", "drop_media") if k in b}
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
        fields = {k: b[k] for k in ("title", "source", "avatar") if k in b}
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
        # one row per EXPORT, not per file: an unpacked account is a hundred files
        # and forty chats torn into pages (see `chat.exports.entries`)
        out = await run_in_threadpool(exports.entries, root)
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
        open_job(run_or_404(run_id), 0)
        return await store_export(file)

    # -- the exports base as a thing of its own, for the configuration panel --
    #
    # The base belongs to the machine and not to a run, so it is managed where the
    # machine's other holdings are (Configuration → chat exports), by routes that need
    # no run. The room's own list is the same list, and its ✕ the same delete.

    def exports_dir() -> Path:
        return store.global_cfg.paths.assets / chat_source.EXPORTS_DIR

    async def store_export(file: UploadFile) -> dict:
        name = Path(str(file.filename or "export")).name
        if not name or name.startswith("."):
            raise HTTPException(status_code=422, detail="that file has no usable name")
        root = exports_dir()
        root.mkdir(parents=True, exist_ok=True)
        body = await file.read()
        if name.lower().endswith(".zip"):
            # The export WITH its pictures, which is a folder and so arrives zipped.
            # Unpacked into a folder of its own beside the others, and answered with
            # the export found inside it — or with the one already there, when this
            # very zip was handed over before.
            try:
                found, again = await run_in_threadpool(_unzip_export, body, root,
                                                       Path(name).stem)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            row = next(e for e in exports.entries(root) if e["name"] == found)
            return {"name": found, "size": len(body), "format": row["format"],
                    "already": again}
        if exports.sniff(body[:1 << 20]) is None:
            raise HTTPException(
                status_code=422,
                detail=f"{name} is not an export this reads: expected Telegram's JSON "
                       "or HTML, a DiscordChatExporter JSON, or a reddit thread's .json")
        at = root / name
        again = at.is_file() and at.read_bytes() == body
        at.write_bytes(body)
        return {"name": name, "size": len(body),
                "format": exports.sniff(body[:1 << 20]) or "", "already": again}

    @app.get("/api/chat/exports")
    async def all_exports(slopgen: str | None = Cookie(default=None)) -> dict:
        """The base, one export to a row, with what each takes on disk — the photos
        and stickers beside it included, since that is what removing it frees — and
        the media base the taken messages' pictures live in."""
        guard(slopgen)
        root = exports_dir()

        def look() -> dict:
            rows = exports.entries(root)
            for r in rows:
                r["disk"] = exports.disk_size(root / r["unit"])
            base = media_base()
            files = [p for p in base.iterdir() if p.is_file()
                     and not p.name.startswith(".")] if base.is_dir() else []
            return {"exports": rows,
                    "media": {"files": len(files), "size": sum(p.stat().st_size for p in files)}}

        return await run_in_threadpool(look)

    @app.post("/api/chat/exports")
    async def add_any_export(file: UploadFile,
                             slopgen: str | None = Cookie(default=None)) -> dict:
        guard(slopgen)
        return await store_export(file)

    @app.delete("/api/chat/exports")
    async def drop_export(unit: str, slopgen: str | None = Cookie(default=None)) -> dict:
        """Remove one export — its whole folder, pictures and all.

        What the videos already made from it keep: the messages taken out of it had
        their pictures copied into the media base when they were taken, so nothing
        that was made goes blank. `unit` is resolved inside the base and nowhere else."""
        guard(slopgen)
        root = exports_dir().resolve()
        at = (root / unit).resolve()
        if root not in at.parents or not at.exists():
            raise HTTPException(status_code=404, detail=f"no export {unit!r} in the base")

        def remove() -> int:
            import shutil

            freed = exports.disk_size(at)
            if at.is_dir():
                shutil.rmtree(at)
            else:
                at.unlink()
            # and the folders that held only it, up to the base
            up = at.parent
            while up != root and up.is_dir() and not any(up.iterdir()):
                up.rmdir()
                up = up.parent
            return freed

        freed = await run_in_threadpool(remove)
        return {"removed": unit, "freed": freed}

    def documents():
        """The text of every saved job there is, for what refers to what."""
        out = Path(store.global_cfg.paths.output)
        for cp in out.rglob("checkpoint.json") if out.is_dir() else []:
            try:
                yield cp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

    @app.get("/api/chat/media/unused")
    async def media_unused(slopgen: str | None = Cookie(default=None)) -> dict:
        """What a tidy-up of the media base would take: the files no saved video
        refers to — pictures of messages looked at and not taken, or of runs since
        deleted."""
        guard(slopgen)
        spare = await run_in_threadpool(lambda: chat_media.unused(media_base(), documents()))
        return {"files": len(spare), "size": sum(p.stat().st_size for p in spare)}

    @app.post("/api/chat/media/clean")
    async def media_clean(slopgen: str | None = Cookie(default=None)) -> dict:
        """Remove those files. Recomputed here rather than taken from the browser, so
        a run saved between the look and the press keeps its pictures."""
        guard(slopgen)

        def clean() -> dict:
            spare = chat_media.unused(media_base(), documents())
            size = 0
            for p in spare:
                size += p.stat().st_size
                p.unlink(missing_ok=True)
            return {"files": len(spare), "size": size}

        return await run_in_threadpool(clean)

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
                    photos=store.global_cfg.paths.assets / chat_render.AVATARS_DIR,
                    media=media_base())
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
        if got is not None and source == "export":
            # the custom emoji of the piece being looked at, fetched through the
            # signed-in account if there is one; the plain fallback if not
            ids = telegram.doc_ids([got])
            found = (await telegram.custom_emoji(store.global_cfg.paths.state, ids,
                                                 media_base()) if ids else {})
            telegram.resolve_custom([got], found)
        return {
            "title": got.title if got else "",
            # the chat's own picture, so that choosing a stretch of it brings the
            # header bar's photo along with the messages
            "avatar": got.avatar if got else "",
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
                 "day": ln.day,
                 "media": media_json(ln.media),
                 "custom": [[a, f] for a, f in ln.custom if not f.startswith("doc:")],
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
        # what each line carries, copied into the media base now that it is taken —
        # and a line that was only a picture whose file did not come is not a line
        carried_all = [await run_in_threadpool(take_media, r.get("media")) for r in rows]
        kept = [(r, m) for r, m in zip(rows, carried_all)
                if str(r.get("text", "")).strip() or m is not None]
        if not kept:
            raise HTTPException(status_code=422, detail="nothing was chosen")
        moved = {int(r.get("i", -1)): n for n, (r, _m) in enumerate(kept)}
        conv = Conversation(title=str(b.get("title", "")).strip(),
                            source=str(b.get("source", "")).strip(),
                            avatar=str(b.get("avatar", "")).strip())
        base = media_base()
        for n, (r, carried) in enumerate(kept):
            was = int(r.get("reply_to", -1))
            custom = [(str(a), str(f)) for a, f in (r.get("custom") or [])
                      if chat_media.resolve(base, str(f)) is not None]
            conv.messages.append(ChatMsg(
                media=carried, custom=custom,
                persona=str(r.get("who", "")).strip(),
                text=str(r.get("text", "")),
                stamp=str(r.get("stamp", "")),
                day=str(r.get("day", "")),
                # The picture rides on the MESSAGE and not on a card, because carding
                # somebody is the operator's decision and an import must not make it
                # for them. When they do card the person, the room offers this as the
                # picture to put on it.
                avatar=str(r.get("avatar", "")),
                score=int(r.get("score", 0) or 0),
                reply_to=moved.get(was, -1) if was >= 0 and moved.get(was, n) != n else -1,
                reactions=[(_safe_reaction(str(e), base), max(1, int(c)))
                           for e, c in (r.get("reactions") or []) if str(e).strip()],
            ))
        job.conversations.append(conv)
        out = answer(cp, i, job)
        out["added"] = 1
        return out

    # -- the people ---------------------------------------------------------

    @app.post("/api/runs/{run_id}/chat/restamp")
    async def restamp(run_id: str, request: Request,
                      slopgen: str | None = Cookie(default=None)) -> dict:
        """Walk the times forward through this conversation in its current order."""
        guard(slopgen)
        b = await body_of(request)
        cp, i, job = edited(run_or_404(run_id), b)
        n = guarded(chatroom.restamp)(job, int(b.get("c", 0)), int(b.get("step", 1)))
        out = answer(cp, i, job)
        out["restamped"] = n
        return out

    def speaks_anywhere(name: str, job, run_id: str) -> bool:
        """Whether anybody still talks under this name: in this video, or in any
        other chat run this machine knows about. Read off the checkpoints, so a run
        that is not open anywhere still counts."""
        if any(m.persona == name for m in job.messages):
            return True
        for other in list(sup.runs.values()):
            if other.id == run_id or other.run_dir is None:
                continue
            if getattr(other.params, "mode", "") != "chat":
                continue
            try:
                cp = Checkpoint.load(other.run_dir)
                jobs = [cp.load_job(int(k)) for k in cp.data.get("jobs", {})]
            except Exception:  # noqa: BLE001 — a run that will not open says nothing
                continue
            if any(j is not None and any(m.persona == name for m in j.messages)
                   for j in jobs):
                return True
        return False

    # -- saying one message again ------------------------------------------
    voice_locks: dict[str, asyncio.Lock] = {}

    @app.post("/api/runs/{run_id}/chat/voice")
    async def revoice_message(run_id: str, request: Request,
                              slopgen: str | None = Cookie(default=None)) -> dict:
        """Voice ONE message again, now, with whatever voice it is cast with.

        The montage room's re-voice does this for the modes whose word timings are
        kept per line. A chat's are on the whole video's clock (`stages.tts`, as for
        an info video), so the take's words are put at the line's start and every
        line after it moves by however much longer or shorter the new take is —
        otherwise the subtitles and the piece-by-piece reveal of every later message
        would run against the wrong voice. The drawing and the cut, both made against
        the old clock, are thrown away for the same reason.

        A message retyped since `script` laid it is voiced as it reads NOW: saying it
        again is exactly what makes the scene and the message agree. One that was not
        laid at all (added afterwards) has no line to voice yet — that is `script`."""
        from ..pipeline.stages import chat_script

        guard(slopgen)
        b = await body_of(request)
        run = run_or_404(run_id)
        cp, i, job = edited(run, b)
        c, at = int(b.get("c", 0)), int(b.get("i", -1))
        conv = guarded(chatroom.conv_at)(job, c)
        if not 0 <= at < len(conv.messages):
            raise HTTPException(status_code=404, detail="no such message")
        msg = conv.messages[at]
        ctx = context(cp)
        spec = chat_script.voice_for(msg, ctx)
        if not spec:
            raise HTTPException(status_code=409,
                                detail="this message has no voice — give it one, or "
                                       "give its person one on their card")
        idx = msg.scene
        if not (0 <= idx < len(job.scenes)) or job.scenes[idx].hush:
            raise HTTPException(status_code=409,
                                detail="this message is not laid on the timeline yet — "
                                       "run «сценарий» first")
        if not msg.text.strip():
            raise HTTPException(status_code=409, detail="there are no words here to say")
        lock = voice_locks.setdefault(run_id, asyncio.Lock())
        if lock.locked():
            raise HTTPException(status_code=409, detail="already voicing a line here")
        async with lock:
            try:
                await run_in_threadpool(chat_script.revoice, job, ctx, msg, spec)
            except Exception as e:  # noqa: BLE001
                log.exception("re-voicing message %d failed", at)
                raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
            job.chat_states = []
            for part in job.parts:
                part.file = None
                part.ass = None
            return answer(cp, i, job)

    @app.post("/api/runs/{run_id}/chat/rename")
    async def rename_person(run_id: str, request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Rename one participant in this conversation, and their card with them.

        The card is renamed only as far as is safe: it outlives this video, so the old
        name's card stays while another chat run still has somebody speaking under it,
        and the new name gets a copy (see below)."""
        guard(slopgen)
        b = await body_of(request)
        run = run_or_404(run_id)
        cp, i, job = edited(run, b)
        was, now = str(b.get("was", "")), str(b.get("now", "")).strip()
        n = guarded(chatroom.rename)(job, was, now)
        # The face goes with the name. Renaming moved the lines and left the card
        # behind, so somebody renamed in the room lost their picture, voice and colour
        # without a word. So the card moves too — unless the new name already has one
        # of its own, which wins — and the old one goes, unless somebody still speaks
        # under it: here, or in another chat run, whose people would otherwise lose
        # their face the way this one used to. A copy left behind "just in case" was
        # a stranger in the list of cards nobody uses, one per rename.
        old = store.personas.get(was)
        if old is not None and now and now != was:
            if now not in store.personas:
                card = old.model_copy(update={"name": now})
                write_config("personas", now, card.model_dump(mode="json"))
                store.personas[now] = card
            if not speaks_anywhere(was, job, run_id):
                delete_config("personas", was)
                store.personas.pop(was, None)
        # …and so does being the account the chat is read from, or their messages
        # would leave the right-hand side the moment they were renamed
        ctx = context(cp)
        if was and ctx.chat.me == was:
            fresh = ctx.chat.model_copy(update={"me": now})
            run.params.manual_chat = fresh
            cp.data["params"]["manual_chat"] = fresh.model_dump(mode="json")
            cp.save()
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
            role=str(b.get("role", was.role if was else "")),
            note=str(b.get("note", was.note if was else "")),
        )
        write_config("personas", name, card.model_dump(mode="json"))
        store.personas[name] = card
        return doc(cp, i, job)

    @app.delete("/api/runs/{run_id}/chat/persona")
    async def uncard_person(run_id: str, request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Throw a card away.

        Only one that nobody in this video speaks through: a card is reached from the
        cast list, and the cast list is built from who talks, so deleting a speaker's
        card would delete the voice and the picture of somebody still on screen and
        leave no way to notice. Whoever is still speaking has to be removed from the
        conversation first, which is the operation that already exists.

        The card is a config and this unlinks the file, so it goes for every video and
        not only this one. That is the point — the cast list fills up with people
        invented for one script, and nothing else offers to clear them out."""
        guard(slopgen)
        b = await body_of(request)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        name = str(b.get("name", "")).strip()
        speaks = any(m.persona == name for c in job.conversations for m in c.messages)
        if speaks:
            raise HTTPException(
                status_code=409,
                detail=f"{name} still speaks in this video — remove their messages first")
        delete_config("personas", name)
        store.personas.pop(name, None)
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
        # The run's own rows first. They change what is burned onto the picture and
        # nothing about the drawing, so they throw away the subtitles and the cut made
        # with them — `subtitles` and `assemble` skip a part that already has its file
        # — and keep the drawn states.
        runs = {k: v for k, v in b.items() if k in RUN_FIELDS}
        if runs:
            if "subtitle_style" in runs:
                runs["subtitle_style"] = runs["subtitle_style"] or None
            base = cp.params.model_dump(mode="json")
            base.update(runs)
            try:
                fresh = type(cp.params).model_validate(base)
            except ValidationError as e:
                raise HTTPException(status_code=422,
                                    detail=f"run settings: {e.errors()[0]['msg']}")
            dumped = fresh.model_dump(mode="json")
            for k in runs:
                cp.data["params"][k] = dumped[k]
                setattr(run.params, k, getattr(fresh, k))
            for part in job.parts:
                part.ass = None
                part.file = None
            cp.save()
            return answer(cp, i, job)
        known = {row["f"] for row in SHEET if "f" in row and not row.get("run")}
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
            # The drawing that exists belongs to the settings that drew it. Every one
            # of these changes the picture — the shape of the frame, the skin, the
            # header, the wallpaper, how far the view rolls — so the states on disk
            # and the bars baked beside them are now pictures of a video this run is
            # no longer making. Leaving them was the whole of what the operator was
            # looking at: the timeline kept scrubbing 9:16 frames with a header on
            # them for a run that had been switched to 16:9 with the header off, and
            # nothing on the screen said the frames were old. `chat_states` is what
            # `montage.SATISFIED` reads to decide `рисование` is done, so dropping it
            # puts the stage back on the table where it belongs, and the preview
            # falls back to drawing each frame live until it has run again.
            cp.save()
            if job.chat_states or any(p.file or p.ass for p in job.parts):
                job.chat_states = []
                # …and the cut that was made FROM those frames, with its subtitles.
                # `assemble` and `subtitles` skip a part that already has a file, so
                # leaving these set meant the stages answered "done" without doing
                # anything and the room went on serving a 9:16 video to a run that had
                # been switched to 16:9 — the clearest form of the same lie: a picture
                # of settings nobody holds any more, with nothing saying so.
                for part in job.parts:
                    part.file = None
                    part.ass = None
                return answer(cp, i, job)
        return doc(cp, i, job)

    def _picture(folder: str, name: str):
        """One picture out of one of the chat mode's picture folders.

        Inside the folder and nowhere else: the name comes off a form, so it is
        resolved against the base and checked to still be under it."""
        root = (store.global_cfg.paths.assets / folder).resolve()
        at = (root / name).resolve()
        if not str(at).startswith(str(root)):
            raise HTTPException(status_code=404, detail="no such picture")
        # …and with an extension on it, because the lists this is called from hold
        # bare names: a card stores `tg_1000561679` and the file is `tg_1000561679.jpg`,
        # which is the same resolution `chat_render.asset` does for the renderer. Only
        # the browser was being made to guess, so every thumbnail in the picker came
        # back 404 and the one control for choosing a picture showed no pictures.
        if not at.is_file():
            for ext in chat_render.IMAGE_EXTS:
                if at.with_name(at.name + ext).is_file():
                    at = at.with_name(at.name + ext)
                    break
        if not at.is_file():
            raise HTTPException(status_code=404, detail="no such picture")
        return FileResponse(at, headers={"Cache-Control": "max-age=300"})

    @app.get("/api/avatar")
    async def avatar(name: str, slopgen: str | None = Cookie(default=None)):
        """One picture out of the avatar base, so the room can show what it is offering."""
        guard(slopgen)
        return _picture(chat_render.AVATARS_DIR, name)

    # The shelves a picture picker may show (see `wirePicks` in app.js): the avatars
    # and the wallpapers, by the name the configs store them under.
    PICTURE_SHELVES = {"avatars": chat_render.AVATARS_DIR,
                       "chat_bg": chat_render.BACKGROUNDS_DIR}

    @app.get("/api/picture/{shelf}")
    async def picture(shelf: str, name: str, slopgen: str | None = Cookie(default=None)):
        guard(slopgen)
        if shelf not in PICTURE_SHELVES:
            raise HTTPException(status_code=404, detail=f"no picture shelf {shelf!r}")
        return _picture(PICTURE_SHELVES[shelf], name)

    @app.get("/api/chat/media")
    async def media_thumb(ref: str = "", name: str = "", size: int = 160,
                          slopgen: str | None = Cookie(default=None)):
        """A small picture of what a message carries, for the lists that show one.

        A PNG of the first frame whatever the file is — a `.tgs` sticker, a WebM, a
        voice message's — because the browser can show a PNG and cannot show half of
        these. `ref` is a picker's reference, `name` a stored message's file."""
        guard(slopgen)
        at = ref_path(ref) if ref else chat_media.resolve(media_base(), name)
        if at is None:
            raise HTTPException(status_code=404, detail="no such file")
        side = max(32, min(int(size), 512))

        def draw() -> bytes | None:
            w, h, _dur, _moves = chat_media.facts(at)
            if not w or not h:
                return None
            k = side / max(w, h)
            pic = chat_media.still(at, max(1, int(w * k)), max(1, int(h * k)))
            if pic is None:
                return None
            buf = io.BytesIO()
            pic.save(buf, "PNG")
            return buf.getvalue()

        png = await run_in_threadpool(draw)
        if png is None:
            raise HTTPException(status_code=404, detail="nothing to show")
        return Response(png, media_type="image/png",
                        headers={"Cache-Control": "max-age=3600"})

    # -- what it will look like ---------------------------------------------

    # -- the reel ------------------------------------------------------------
    #
    # The video, played in the page rather than encoded (see `pipeline.chat_reel`).
    # Built on request, one build per run at a time — a second request while one is
    # drawing waits for it rather than drawing the same states beside it — and the
    # drawings it makes are the render stage's own, so a second build after an edit
    # redraws only what the edit changed on screen.
    reel_locks: dict[str, asyncio.Lock] = {}
    reel_files: dict[str, dict] = {}   # run id -> {file id: (path, kind)}
    reel_memo: dict[str, tuple] = {}   # "run:video" -> (what it was of, the reel)

    @app.get("/api/runs/{run_id}/chat/reel")
    async def reel(run_id: str, video: int = 0, slopgen: str | None = Cookie(default=None)):
        from ..pipeline import chat_reel

        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        if not job.messages:
            raise HTTPException(status_code=404, detail="there is no conversation yet")
        ctx = context(cp)
        lock = reel_locks.setdefault(f"{run_id}:{video}", asyncio.Lock())
        async with lock:
            want = chat_reel.key(job, ctx)
            was = reel_memo.get(f"{run_id}:{video}")
            if was is not None and was[0] == want:
                return was[1]
            try:
                got = await run_in_threadpool(chat_reel.build, job, ctx)
            except Exception as e:  # noqa: BLE001
                log.exception("building the chat reel failed")
                raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
            out = _reel_json(run_id, got, reel_files.setdefault(run_id, {}))
            reel_memo[f"{run_id}:{video}"] = (want, out)
            return out

    @app.get("/api/runs/{run_id}/chat/reel/f/{fid}")
    async def reel_file(run_id: str, fid: str, slopgen: str | None = Cookie(default=None)):
        guard(slopgen)
        path, kind = reel_files.get(run_id, {}).get(fid, (None, ""))
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="not part of a reel")
        if kind == "state":
            path = await run_in_threadpool(_as_webp, path)
        return FileResponse(path, headers={"Cache-Control": "max-age=86400"})

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
        # In memory and not a file in the run: the room asks for the next frames
        # ahead of the playhead, several at once, and one `preview.png` shared by all
        # of them handed each request whichever frame finished last.
        out = io.BytesIO()
        # A message named outright wins over a moment: they are two ways of asking
        # the same question and only one of them can be answered, so the more specific
        # one is. Belt and braces after a caller sent both and meant the first.
        if at >= 0:
            t = -1.0
        try:
            if t >= 0 and job.chat_states:
                await run_in_threadpool(_frame_at, job, ctx, float(t), out)
            else:
                # A moment asked for before anything has been drawn — or after a
                # settings change threw the drawing away — is still a moment worth
                # answering. It becomes the message that is up then, so the strip goes
                # on scrubbing instead of dying the instant the skin is changed, and
                # what it shows is drawn fresh and therefore true.
                last = (_message_at(job, float(t)) if t >= 0 else
                        (len(job.messages) - 1 if at < 0
                         else max(0, min(at, len(job.messages) - 1))))
                await run_in_threadpool(_draw_preview, job, ctx, last, out)
        except Exception as e:  # noqa: BLE001
            log.exception("drawing the chat preview failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return Response(out.getvalue(), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


def _reel_json(run_id: str, reel: dict, files: dict) -> dict:
    """The reel with every path swapped for a URL the page may fetch, and the swap
    remembered in `files`: the file route serves what is in there and nothing else, so
    a reel is not a way to name an arbitrary path on this machine."""
    import hashlib

    def url(path, kind: str) -> str:
        # the file's age is in the name: a take re-voiced in place is a new URL, and
        # the day the browser may keep a file for cannot hand back the old voice
        try:
            age = Path(path).stat().st_mtime_ns
        except OSError:
            age = 0
        fid = hashlib.sha1(f"{kind}|{path}|{age}".encode()).hexdigest()[:20]
        files[fid] = (Path(path), kind)
        return f"/api/runs/{run_id}/chat/reel/f/{fid}"

    out = {k: reel[k] for k in ("exact", "total", "width", "height", "top", "marks", "durs",
                                "subs")}
    out["states"] = [{
        **{k: st[k] for k in ("start", "dur", "msg", "from_y", "to_y", "swipe", "travel")},
        "src": url(st["path"], "state"),
        "overlay": url(st["overlay"], "image") if st["overlay"] else None,
        "ground": url(st["ground"], "image") if st["ground"] else None,
        "tiles": [{**{k: t[k] for k in ("x", "y", "w", "h", "t0", "length")},
                   "src": url(t["path"], "clip" if t["length"] > 0 else "image"),
                   "poster": url(t["poster"], "image") if t.get("poster") else None}
                  for t in st["tiles"]],
    } for st in reel["states"]]
    out["under"] = [{"src": url(u["path"], "clip"), "at": u["at"], "dur": u["dur"],
                     "seek": u["seek"]} for u in reel["under"]]
    out["sounds"] = [{"src": url(x["path"], "sound"), "at": x["at"], "dur": x["dur"],
                      "rate": x["rate"], "gain": x["gain"], "kind": x["kind"]}
                     for x in reel["sounds"]]
    m = reel["music"]
    out["music"] = {"src": url(m["path"], "sound"), "gain": m["gain"]} if m else None
    return out


def _as_webp(png: Path) -> Path:
    """A drawn state as a WebP beside it, made once. The states are written fast and
    therefore fat (`compress_level=1`) — megabytes each, a hundred of them to a video —
    and the page needs to look at them, not to keep them lossless. WebP and not JPEG
    because a state over a wallpaper is see-through (see `Canvas.band`)."""
    out = png.with_name(png.stem + ".view.webp")
    if not out.is_file() or out.stat().st_mtime < png.stat().st_mtime:
        with Image.open(png) as img:
            tmp = out.with_suffix(".tmp")
            img.save(tmp, "WEBP", quality=86)
            tmp.replace(out)
    return out


def _safe_reaction(emoji: str, base: Path) -> str:
    """A reaction off the browser, with a custom one kept only if its file is really
    in the media base — otherwise it is its plain fallback."""
    alt, ref = exports.split_reaction(emoji)
    if not ref:
        return emoji
    return emoji if chat_media.resolve(base, ref) is not None else alt


# Written into each folder a zip was unpacked into: the zip's own hash, so the same
# zip handed over twice is recognised instead of unpacked a second time beside itself
# as `name_2` — which is how a base ends up holding a 120 MB export twice.
ZIP_MARK = ".slopgen-zip"


def _unzip_export(body: bytes, root: Path, stem: str) -> tuple[str, bool]:
    """Unpack a zipped export into a folder of its own under the base, and return the
    export inside it (as a name relative to the base) and whether it was already
    there.

    Every member is checked to land inside that folder: a zip is somebody else's
    archive, and `../../` in a member name is the oldest way out of one."""
    import hashlib
    import zipfile

    digest = hashlib.sha1(body).hexdigest()
    for mark in root.glob(f"*/{ZIP_MARK}"):
        try:
            same = mark.read_text().strip() == digest
        except OSError:
            same = False
        if same:
            name = _export_in(root, mark.parent)
            if name:
                return name, True
    try:
        zf = zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise ValueError("that zip will not open")
    safe = "".join(ch for ch in stem if ch.isalnum() or ch in "-_. ").strip() or "export"
    dest = root / safe
    n = 1
    while dest.exists():
        n += 1
        dest = root / f"{safe}_{n}"
    dest.mkdir(parents=True)
    inside = dest.resolve()
    for info in zf.infolist():
        if info.is_dir():
            continue
        at = (dest / info.filename).resolve()
        if inside not in at.parents:
            continue
        at.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(at, "wb") as out:
            while chunk := src.read(1 << 20):
                out.write(chunk)
    name = _export_in(root, dest)
    if name:
        (dest / ZIP_MARK).write_text(digest)
        return name, False
    import shutil

    shutil.rmtree(dest, ignore_errors=True)
    raise ValueError("nothing in that zip is an export this reads")


def _export_in(root: Path, folder: Path) -> str:
    """Whatever the base lists inside this folder — the folder itself for an HTML
    export, its `result.json` for a JSON one — or "" for nothing readable."""
    inside = folder.relative_to(root).as_posix()
    for e in exports.entries(root):
        if e["format"] and (e["name"] == inside or e["name"].startswith(inside + "/")):
            return e["name"]
    return ""


def _message_at(job, when: float) -> int:
    """Which message is up at `when`, by the scene clock the strip is marked with.

    The same arithmetic `_clock` numbers the marks with, so the frame that comes back
    is the one the mark under the playhead names."""
    at, start = 0.0, []
    for scene in job.scenes:
        start.append(at)
        at += scene.duration
    last = 0
    for n, msg in enumerate(job.messages):
        if 0 <= msg.scene < len(start) and start[msg.scene] <= when + 1e-6:
            last = n
    return max(0, min(last, len(job.messages) - 1))


def _frame_at(job, ctx: AppContext, when: float, out) -> None:
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
    img = Image.open(st.path).convert("RGBA")
    top = chat_render.chat_height(ctx)
    span = max(cfg.swipe_s if st.swipe else cfg.roll_s, 1e-3)
    k = min(max((at - st.start) / span, 0.0), 1.0)
    y = st.from_y + (st.to_y - st.from_y) * k
    x = (v.width * k) if st.swipe else 0.0
    x = min(max(x, 0), max(img.width - v.width, 0))
    y = min(max(y, 0), max(img.height - top, 0))
    if not st.swipe:
        # the stickers that are only their clip in the drawing (see `media.hollow`)
        img = chat_render.with_posters(img, st.tiles)
    frame = img.crop((int(x), int(y), int(x) + v.width, int(y) + top))
    # a see-through drawing goes onto its wallpaper, which the crop does not move
    under = (Image.open(st.ground).convert("RGB")
             if st.ground and Path(st.ground).is_file()
             else Image.new("RGB", frame.size, "#000000"))
    under.paste(frame, (0, 0), frame)
    frame = under
    # A swipe already carries both bars inside the picture; everything else wears its
    # own as an overlay, exactly as the assembler lays it (see `chat_render`).
    if st.overlay and not st.swipe and Path(st.overlay).is_file():
        with Image.open(st.overlay) as bar:
            bar = bar.convert("RGBA")
            frame.paste(bar, (0, 0), bar)
    _framed(frame, v, top).save(out, format="PNG")


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


def _draw_preview(job, ctx: AppContext, last: int, out) -> None:
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
                      top_inset=skin.header_h if cfg.header else 0,
                      day_label=lambda iso: chat_render.day_label(iso, ctx.params.lang))
    canvas: Canvas = planner.lay_upto(last)
    # the bottom of the conversation, even when the conversation is shorter than the
    # screen and the number is negative: the list hangs from the bottom edge the way
    # a messenger's does (see `scroll.Planner._rest`)
    frame = canvas.band(int(canvas.height - top), top)
    if cfg.header:
        conv = next((c for c in job.conversations
                     if any(m is job.messages[last] for m in c.messages)), None)
        bar = canvas.header_image(
            (conv.title if conv else "") or cfg.title or job.chat_title or "",
            chat_render.asset(ctx, chat_render.AVATARS_DIR,
                              (conv.avatar if conv else "") or cfg.header_avatar))
        frame.paste(bar, (0, 0), bar)
    _framed(frame, v, top).save(out, format="PNG")
