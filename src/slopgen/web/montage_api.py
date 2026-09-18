"""The montage screen's routes.

Split out of `web/app.py` for the reason `params.py` and `runs.py` were: it is a
screen's worth of endpoints — fifteen of them — and a screen's worth of endpoints
inside a file that is already every other screen makes both harder to read than
either is alone. What it needs from the app it is handed (:func:`mount`), so there is
still exactly one place where a session is checked and one place where a run is
looked up.

Every route here writes the job of a PARKED run and leaves it parked. That is the
whole discipline of the module: the pipeline owns a job while it is moving, this owns
it while it is not, and `save` is the door between the two — it refuses a run that is
neither paused nor waiting on a breakpoint, and it puts back exactly the state it
found rather than stamping one of its own.

The two rendering routes are the odd ones out and both exist because a browser cannot
answer the question by itself. `/audio` builds the whole voice track as one file so
the preview has a clock that does not drift; `/still` renders one frame through the
real ffmpeg chain so the filter sliders are answerable with something better than a
canvas approximation of `noise` and `curves`.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from fastapi import Cookie, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..config import ConfigStore
from ..media import ffmpeg
from ..media import filters as fxmod
from ..media.stock import IMAGE_EXTS, VIDEO_EXTS
from ..pipeline import montage, orchestrator
from ..pipeline.checkpoint import Checkpoint
from ..pipeline.context import AppContext
from ..pipeline.manual import ManualInputPending
from ..pipeline.stages import metadata as metadata_stage
from ..pipeline.stages import picture

log = logging.getLogger(__name__)

PICTURE_SUFFIXES = IMAGE_EXTS | VIDEO_EXTS
# what a hand-recorded line may arrive as. Wider than a synthesizer's output on
# purpose: this is a phone's memo or whatever a voice service downloaded as.
VOICE_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm"}

# One hand-pressed stage per run at a time. Per RUN and not per process, because two
# runs being worked on in two tabs is an ordinary thing and neither touches the other's
# job; two stages over ONE job is the case where the second to finish wins by accident.
_locks: dict[str, threading.Lock] = {}


def mount(app, *, store: ConfigStore, sup, guard, run_or_404, card_json) -> None:
    """Hang the montage routes on the app, using its session guard, its run lookup and
    its supervisor — the last because a stage pressed here reports where a stage run by
    the chain reports: the run's own log and the run's own bar."""

    # -- reaching the job ---------------------------------------------------

    def open_job(run, video: int):
        """`(checkpoint, index, job)` for one video of a run that is not moving.

        Re-read from disk on every request rather than held. The checkpoint is the
        authority on what a parked run knows, exactly as the manifest is the authority
        on what has been delivered, and a cached job would go stale the moment the run
        was resumed from anywhere else — the terminal, another tab, `slopgen review`."""
        if run.run_dir is None:
            raise HTTPException(status_code=409, detail="this run has no folder yet")
        if run.status in ("running", "queued"):
            raise HTTPException(status_code=409,
                                detail="this run is moving — stop it before editing it")
        cp = Checkpoint.load(run.run_dir)
        job = cp.load_job(video)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no video {video} in this run")
        return cp, video, job

    def save(cp: Checkpoint, i: int, job, done: list[str] | None = None) -> None:
        """Write an edited job back and leave the run exactly as parked as it was.

        `done` is the completed-stage list when a stage has just been run by hand, and
        the checkpoint's own otherwise. It is the same list a resume reads, so a stage
        pressed here counts as run and a chain picked up afterwards walks past it.

        Two states may be written and they are not interchangeable. A run waiting on
        pictures is `paused` and carries the note that says what it is waiting for; a
        run sitting on a breakpoint is `review` and carries WHICH breakpoint, and
        stamping that one `paused` would lose the thing it is parked on. Anything else
        — running, finished, failed — is refused: a job rewritten under a finished run
        describes a video that has already been cut."""
        status = cp.status(i)
        # Read off the JOB, not merely carried: work the operator did by hand IS a
        # stage's output, and a stage whose output is there must not be run again by a
        # resume (see `montage.completed`). Without this, a script typed in this room
        # left `script` unfinished and resuming the run threw every typed line away.
        done = montage.completed(job, cp.completed(i) if done is None else done)
        if status == "review":
            cp.awaiting_review(job, done, cp.review_stage(i))
        elif status == "paused":
            cp.paused(job, done, "", cp.manual_msg(i))
        else:
            raise HTTPException(
                status_code=409,
                detail="this video is not parked — there is nothing to edit here")

    def world_of(run):
        """The world this run's pictures come out of. Only fandom has a frame base,
        and a montage without one is a track nothing can be cast from."""
        world = store.fandoms.get(run.params.fandom) if run.params.mode == "fandom" else None
        if world is None:
            raise HTTPException(status_code=409, detail="this run has no frame base")
        return world

    def context(cp: Checkpoint) -> AppContext:
        """A context for the one stage this screen re-runs by hand: the synthesizer.

        Built per call, and only where a voice is actually being made. Constructing one
        opens an LLM client, which is right for a stage and wrong for everything else
        here — casting a card is a question about geometry, and it must not be the
        thing that fails because a text model has no key."""
        return AppContext(store=store, params=cp.params)

    async def body_of(request: Request) -> dict:
        try:
            b = await request.json()
        except Exception:
            b = {}
        return b if isinstance(b, dict) else {}

    def doc(run, cp: Checkpoint, i: int, job) -> dict:
        """The answer every editing route gives: the whole document, again.

        Not a patch. Almost every edit here moves the clock — a re-voiced line shifts
        every cut after it, a dropped cut lengthens its neighbour, a placed one splits
        it — so a reply describing only what was asked for would leave the screen
        holding numbers that are no longer true. The document is small (a minute of
        video is forty lines and a dozen shots) and being right is worth more than
        being short."""
        world = store.fandoms.get(run.params.fandom) if run.params.mode == "fandom" else None
        out = montage.read(job, cp.params)
        out["video"] = i
        out["stage"] = cp.review_stage(i)
        out["stages"] = montage.stages(job, cp.params,
                                       montage.completed(job, cp.completed(i)))
        out["cut"] = bool(job.final_paths)
        out["world"] = run.params.fandom if world is not None else ""
        out["cards"] = [card_json(run.params.fandom, c)
                        for c in (world.frames if world else []) if c.usable]
        # Whether a shot's picture is too small to crop without softening it — asked
        # of the SHOT and not of every card in the world, which is both where it is
        # used and the difference between one probe and thirty-three. The answer costs
        # an `ffprobe` the first time it is asked about a file (cached against the file
        # afterwards, see `media/ffmpeg.video_dims`), and a base of thirty-three cards
        # probed on every reply is what made every button in this room take two
        # seconds. Measured, and the reason this is not a loop over the base.
        by_name = {c.name: c for c in (world.frames if world else [])}
        asked: dict[str, bool] = {}
        for row in out["shots"]:
            name = row["card"]
            card = by_name.get(name)
            if card is None:
                continue
            if name not in asked:
                asked[name] = picture.soft(card, store.global_cfg)
            row["soft"] = asked[name]
        return out

    # -- the document -------------------------------------------------------

    @app.get("/api/runs/{run_id}/montage")
    async def read_montage(run_id: str, video: int = 0,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """The timeline: the lines, their words on one clock, the shots over them,
        the base to cast from and the look laid over all of it."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        return doc(run, cp, i, job)

    # -- the track's two halves ---------------------------------------------

    @app.post("/api/runs/{run_id}/montage/text")
    async def set_text(run_id: str, request: Request,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Rewrite what a line SAYS, leaving what it sounds like alone."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        line = int(b.get("scene", -1))
        if not 0 <= line < len(job.scenes):
            raise HTTPException(status_code=404, detail="no such line")
        montage.set_text(job, line, str(b.get("text", "")))
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/voice")
    async def revoice(run_id: str, request: Request,
                      slopgen: str | None = Cookie(default=None)) -> dict:
        """Say this line again — at another speed, or simply as it now reads.

        Off the request thread: one line of synthesis is a network call to a voice
        service (or a local model with 2.3 GiB of weights), and the event loop here is
        also serving the page that is waiting for it."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        line = int(b.get("scene", -1))
        if not 0 <= line < len(job.scenes):
            raise HTTPException(status_code=404, detail="no such line")
        # asked before the context is built, and the order is the whole point: making
        # one opens an LLM client, so an empty line answered from in there comes back
        # as whatever the text model has to say about its API key
        if not job.scenes[line].text.strip():
            raise HTTPException(status_code=409,
                                detail="there is nothing written on this line to say")
        rate = b.get("rate")
        try:
            await run_in_threadpool(
                montage.voice, job, context(cp), line,
                int(rate) if rate is not None and str(rate) != "" else None)
        except Exception as e:
            log.exception("re-voicing line %d failed", line)
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/voice/file")
    async def take_voice(run_id: str, video: int = 0, scene: int = -1,
                         file: UploadFile = None,
                         slopgen: str | None = Cookie(default=None)) -> dict:
        """Read the line yourself and hand it over. The timings come from the aligner,
        the way they do for a whole video voiced by hand."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        if not 0 <= scene < len(job.scenes):
            raise HTTPException(status_code=404, detail="no such line")
        suffix = Path(file.filename or "").suffix.lower() if file else ""
        if suffix not in VOICE_SUFFIXES:
            raise HTTPException(status_code=415,
                                detail=f"{suffix or 'that'} is not a sound file")
        tmp = Path(job.workdir) / "tts" / f"incoming_{scene:02d}{suffix}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            await run_in_threadpool(montage.take_voice, job, context(cp), scene, tmp)
        except Exception as e:
            log.exception("taking a recording for line %d failed", scene)
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        finally:
            tmp.unlink(missing_ok=True)
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/line")
    async def add_line(run_id: str, request: Request,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Put a new beat into the video, after the line given (-1 = at the front).

        It arrives with no voice and therefore no length, so nothing on the clock moves
        until it is voiced — and until then the screen counts it among the things that
        are not finished."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        # the slot only matters for the FIRST line of a video written from nothing —
        # every later one takes its neighbour's — but that is the line a hand-made run
        # always starts with (see `montage.default_slot`)
        slot = montage.default_slot(AppContext(store=store, params=cp.params))
        at = montage.add_line(job, int(b.get("after", -1)), str(b.get("text", "")), slot)
        save(cp, i, job)
        out = doc(run, cp, i, job)
        out["at"] = at
        return out

    @app.delete("/api/runs/{run_id}/montage/line")
    async def drop_line(run_id: str, video: int = 0, scene: int = -1,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Take one line out of the video. Its seconds go with it and every cut after
        it moves up; a picture that covered it now starts where the next line does."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        try:
            montage.drop_line(job, scene)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        return doc(run, cp, i, job)

    # -- where the picture changes ------------------------------------------

    @app.post("/api/runs/{run_id}/montage/cut")
    async def place_cut(run_id: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Start a shot on one word. Whatever was up ends there."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        try:
            at = montage.place_cut(job, int(b.get("scene", -1)), int(b.get("word", -1)))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        out = doc(run, cp, i, job)
        out["at"] = at
        return out

    @app.delete("/api/runs/{run_id}/montage/cut")
    async def drop_cut(run_id: str, video: int = 0, shot: int = -1,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Take one cut back: this shot joins the one in front of it."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        try:
            montage.drop_cut(job, shot)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/recut")
    async def recut(run_id: str, request: Request,
                    slopgen: str | None = Cookie(default=None)) -> dict:
        """Cut the whole track out of the speech again, at this sensitivity.

        It throws away every card with the cuts, pins included, because a card was
        chosen for a stretch and the stretches are what this replaces. The sensitivity
        is written back onto the run, so a later re-run of the stage cuts the same way
        this did rather than the way the form was filled in."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        sens = float(b.get("sensitivity", cp.params.cut_sensitivity))
        montage.recut(job, sens)
        cp.data["params"]["cut_sensitivity"] = min(max(sens, 0.0), 1.0)
        run.params.cut_sensitivity = min(max(sens, 0.0), 1.0)
        save(cp, i, job)
        return doc(run, cp, i, job)

    # -- what is shown ------------------------------------------------------

    @app.put("/api/runs/{run_id}/montage/shot")
    async def cast_shot(run_id: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Put a picture on a shot, or take one off it, and say what the camera does.

        An empty card name clears the shot — which is a real answer and not a mistake:
        a run whose base does not cover a stretch is a run that is about to ask for a
        picture, and clearing a card the matcher guessed badly is how you ask."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        world = world_of(run)
        name = str(b.get("card", "")).strip()
        card = next((c for c in world.frames if c.name == name and c.usable), None)
        if name and card is None:
            raise HTTPException(status_code=404,
                                detail=f"no usable card named {name!r} in this world")
        num = lambda k: (float(b[k]) if b.get(k) is not None and b[k] != "" else None)
        try:
            # no `min_scale`: a hand-cast shot is aimed at a region the operator
            # marked, and the quality floor would re-centre it (see `montage.cast`)
            montage.cast(job, int(b.get("shot", -1)), card,
                         move=str(b.get("move", "")), target=str(b.get("target", "")),
                         lead=num("lead"), span=num("span"))
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/moves")
    async def refresh_moves(run_id: str, request: Request,
                            slopgen: str | None = Cookie(default=None)) -> dict:
        """Build every shot's move again, keeping the cards, kinds and timings.

        The repair for a track decided under the old crop floor, which forbade any
        enlargement and so collapsed every push-in, zoom and pan into a frozen frame.
        Nothing re-plans a pinned shot, and every hand-cast shot is pinned, so without
        this the only way back is re-clicking every card on the track."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        world = world_of(run)
        cards = [c for c in world.frames if c.usable]
        n = montage.refresh_moves(job, cards)
        save(cp, i, job)
        out = doc(run, cp, i, job)
        out["fixed"] = n
        return out

    @app.put("/api/runs/{run_id}/montage/shot/keys")
    async def set_keys(run_id: str, request: Request,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Replace a shot's crop move with the moments the operator placed.

        The whole list every time rather than one key at a time: keys are ordered by
        when they happen, so an index is not a stable name for one — drag the second
        past the third and every id after it means something else. A list has no such
        problem and no races with itself."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        world = world_of(run)
        shot = int(b.get("shot", -1))
        name = job.frame_shots[shot].card if 0 <= shot < len(job.frame_shots) else ""
        card = next((c for c in world.frames if c.name == name and c.usable), None)
        try:
            montage.set_keys(job, shot, card, list(b.get("keys") or []))
        except (ValueError, IndexError) as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        return doc(run, cp, i, job)

    @app.post("/api/runs/{run_id}/montage/shot/card")
    async def new_card(run_id: str, video: int = 0, shot: int = -1,
                       description: str = "", file: UploadFile = None,
                       slopgen: str | None = Cookie(default=None)) -> dict:
        """Bring a picture for a stretch that has none: it joins the world's base and
        goes straight onto the shot.

        Into the BASE and not into this video, which is the whole economy of the mode:
        a picture made for one stretch is spent again in the next video, so there is
        nowhere else for it to go. It arrives with no regions marked, so it can only be
        held, pushed into or drifted across until somebody draws some — which is what
        the reply's card name is for."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, i, job = open_job(run, video)
        world = world_of(run)
        suffix = Path(file.filename or "").suffix.lower() if file else ""
        if suffix not in PICTURE_SUFFIXES:
            raise HTTPException(status_code=415,
                                detail=f"{suffix or 'that'} is not a picture or a clip")
        tmp = Path(job.workdir) / "montage" / f"incoming{suffix}"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
        card = montage.take_card(world, tmp, description=description,
                                 note="взята в монтажной")
        tmp.unlink(missing_ok=True)
        try:
            montage.cast(job, shot, card)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
        save(cp, i, job)
        out = doc(run, cp, i, job)
        out["card"] = card.name
        return out

    # -- the look -----------------------------------------------------------

    @app.put("/api/runs/{run_id}/montage/settings")
    async def set_settings(run_id: str, request: Request,
                           slopgen: str | None = Cookie(default=None)) -> dict:
        """The run's own settings, as far as this screen may change them.

        Onto the run's PARAMS rather than onto the job, because that is where they live
        and where the stages read them from. Both copies are moved: the checkpoint on
        disk, which is what a resume and the next hand-pressed stage read, and the
        object this server is holding, which is what the run list shows.

        Three of them, and they are here because each is a question the montage room is
        the right place to ask. The LOOK, because the sketch beside the sliders is the
        only honest way to choose it. The SENSITIVITY, because it is what `нарезать
        заново` re-cuts at. And BY HAND, because "do I want the matcher's opinion at
        all" is a montage question, and leaving it on a form the operator can no longer
        see makes one button on this screen quietly mean two different things."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        if "filters" in b:
            spec = fxmod.normalise(b.get("filters") or {})
            cp.data["params"]["filters"] = spec
            run.params.filters = dict(spec)
        if "frame_by_hand" in b:
            on = bool(b.get("frame_by_hand"))
            cp.data["params"]["frame_by_hand"] = on
            run.params.frame_by_hand = on
        if "cut_sensitivity" in b:
            sens = min(max(float(b.get("cut_sensitivity", 0.35)), 0.0), 1.0)
            cp.data["params"]["cut_sensitivity"] = sens
            run.params.cut_sensitivity = sens
        cp.save()
        return doc(run, cp, i, job)

    # -- seeing it ----------------------------------------------------------

    @app.get("/api/runs/{run_id}/montage/audio")
    async def voice_track(run_id: str, video: int = 0,
                          slopgen: str | None = Cookie(default=None)):
        """Every line's voice as one file — the preview's clock.

        Rebuilt when a line has been re-voiced since it was last made, and not
        otherwise: the test is the newest piece against the file, which is exactly the
        thing that changes when somebody presses the button that changes it."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, _i, job = open_job(run, video)
        pieces = montage.voice_pieces(job)
        out = Path(job.workdir) / "montage" / "voice.m4a"
        newest = max((p.stat().st_mtime for p, _s in pieces if p), default=0.0)
        if not out.is_file() or out.stat().st_mtime < newest:
            try:
                await run_in_threadpool(ffmpeg.voice_track, pieces, out, store.global_cfg)
            except Exception as e:
                log.exception("building the preview voice track failed")
                raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return FileResponse(out, media_type="audio/mp4",
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/runs/{run_id}/montage/still")
    async def still(run_id: str, video: int = 0, at: float = 0.0,
                    slopgen: str | None = Cookie(default=None)):
        """One frame of the finished video, rendered by the thing that will render it.

        The screen's own preview is a canvas approximation, and for placing cuts that
        is the right trade — it is instant. For the LOOK it is not: grain, a tube's
        misregistered colour, a torn scanline are ffmpeg's arithmetic and a canvas can
        only impersonate them. So the sliders are answerable with the truth, one frame
        at a time, at whatever moment the playhead is on."""
        guard(slopgen)
        run = run_or_404(run_id)
        cp, _i, job = open_job(run, video)
        world = world_of(run)
        shot, into = montage.card_at(job, max(at, 0.0))
        if shot is None or not shot.card:
            raise HTTPException(status_code=404, detail="there is no picture at that moment")
        card = next((c for c in world.frames if c.name == shot.card and c.usable), None)
        if card is None or card.path is None:
            raise HTTPException(status_code=404, detail=f"card {shot.card!r} has no file")
        out = Path(job.workdir) / "montage" / "still.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        photo = card.path.suffix.lower() in IMAGE_EXTS
        try:
            await run_in_threadpool(
                ffmpeg.still_frame, card.path, out, store.global_cfg,
                seconds=into, shot_s=shot.duration, move=shot.move if photo else None,
                fit=card.fit, ax=card.fit_x, ay=card.fit_y,
                fx=cp.params.filters, photo=photo)
        except Exception as e:
            log.exception("rendering a true frame failed")
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        return FileResponse(out, media_type="image/jpeg",
                            headers={"Cache-Control": "no-store"})

    # -- driving the pipeline by hand ---------------------------------------

    @app.post("/api/runs/{run_id}/montage/stage")
    async def run_stage(run_id: str, request: Request,
                        slopgen: str | None = Cookie(default=None)) -> dict:
        """Run ONE stage of the chain, now, on this video.

        The chain is an order and this screen is not: the work really goes write, hear
        it, look at it, re-write that line, voice it again, cut, cast, cut again — and
        every one of those is a stage the pipeline already has, reached in the order the
        work takes rather than the order the list is written in. So the same callable
        the orchestrator would have called is called here, on the same job, and recorded
        in the same completed list, which is what lets a run driven by hand be resumed
        by the chain afterwards and walk past what is already done.

        It reports on the RUN's own stream — the log under the row, the bar over it —
        because that is where a stage's progress already goes and the operator may well
        be watching it there rather than here; voicing forty lines is a minute in which
        this request says nothing at all.

        One at a time, per run. Two stages over one job would each write the other's
        work away, and the second to finish would win by accident."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        stage = str(b.get("stage", ""))
        fn = dict(orchestrator.stages_for(cp.params)).get(stage)
        if fn is None:
            raise HTTPException(status_code=404,
                                detail=f"there is no stage called {stage!r} in this mode")
        # `metadata` is the one stage that asks whether the run wanted it at all
        # (`RunParams.write_metadata`), and a press here IS that answer — the operator
        # is standing in front of the button. Its own entry point would read the switch
        # instead and leave the press doing nothing, two screens away from anything
        # that explains why.
        if stage == "metadata":
            fn = metadata_stage.write_all
        lock = _locks.setdefault(run_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409,
                                detail="a stage is already running on this video")
        was, run.status = run.status, "running"
        sup.announce(run, i, stage, "start", "by hand")
        ctx = None
        try:
            ctx = AppContext(store=store, params=cp.params,
                             on_progress=sup.progress_sink(run))
            ctx.usage.stage = stage
            await run_in_threadpool(fn, job, ctx)
        except ManualInputPending as e:
            # not a failure: the stage did what it could and is owed material. The job
            # is saved all the same — what it planned is the thing being asked for.
            sup.announce(run, i, stage, "paused", str(e))
            run.status = was
            save(cp, i, job)
            out = doc(run, cp, i, job)
            out["ran"] = stage
            out["waiting"] = str(e)
            return out
        except Exception as e:
            log.exception("stage %s failed under the montage room", stage)
            sup.announce(run, i, stage, "error", f"{type(e).__name__}: {e}")
            run.status = was
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")
        finally:
            if ctx is not None:
                ctx.usage.stage = ""
            lock.release()
            if run.status == "running":
                run.status = was
        sup.announce(run, i, stage, "done", "by hand")
        montage.settle(job)
        done = cp.completed(i)
        if stage not in done:
            done.append(stage)
        save(cp, i, job, done)
        out = doc(run, cp, i, job)
        out["ran"] = stage
        return out

    # -- letting it go ------------------------------------------------------

    @app.post("/api/runs/{run_id}/montage/apply")
    async def apply(run_id: str, request: Request,
                    slopgen: str | None = Cookie(default=None)) -> dict:
        """Finish with the montage and let the run walk on.

        The breakpoint is marked done WITHOUT re-running the stage, which is the one
        thing that matters here: `stages.picture` re-plans everything unpinned on every
        pass, and reporting this edit as stale would send the montage straight back to
        the matcher that the operator has just spent an hour overruling.

        It refuses while anything would fail to render — an unvoiced line is a
        zero-length segment, a shot with nothing on it is a stretch of missing picture
        — because the alternative is an ffmpeg error twenty minutes later that names
        neither."""
        guard(slopgen)
        run = run_or_404(run_id)
        b = await body_of(request)
        cp, i, job = open_job(run, int(b.get("video", 0)))
        left = montage.blocking(job)
        if left and not b.get("anyway"):
            raise HTTPException(status_code=409, detail="; ".join(
                f"{x['what']}: {x['n']}" for x in left))
        stage = cp.review_stage(i)
        if stage:
            cp.review_done(job, cp.completed(i), stage, rerun=False)
        else:
            cp.paused(job, cp.completed(i), "", cp.manual_msg(i))
        return {"ok": True, "stage": stage, "run_dir": str(run.run_dir)}
