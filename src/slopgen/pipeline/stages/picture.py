"""Stage: plan the picture track out of the world's frame base.

This runs between `tts` and `footage`, and the order is the point. Planning here
means the operator sees the whole picture track — every cut, every card, every move —
and can pin what they want BEFORE anything is asked of them. Doing it inside the
footage stage, as the first draft did, meant the run stopped to demand five new
pictures from somebody who had not yet been shown the plan those pictures were for.

Two judgements are made here and they are kept apart on purpose.

WHERE the picture changes is decided in :mod:`..framebase` by the speech alone — by
where the speaker breathes — and it sees no cards at all. That is what stops a
delivered picture from moving a cut that was already agreed, which matters because the
base grows during a run: the operator hands over what was asked for, and the plan is
made again.

WHAT is shown is decided here, by a model reading each card's description against each
stretch of narration. It is a question about meaning, and the only alternative — word
overlap between the prompt that drew the card and the words being spoken — cannot tell
that «торг» and «рынок» are the same place, or that a hat mentioned in the narration is
the hat marked on the picture. What the model may NOT decide is rhythm: how often a
card may come back, and whether this move looks like the last one, are handled by
`framebase.Picker`, which knows nothing about meaning and is better for it.
"""

from __future__ import annotations

import logging
import random
import re
import shutil
from pathlib import Path

from ...config.models import FIT_ACCEPTS, FrameCard
from ...config.loader import card_is_stale, file_sha, frames_dir, write_frame_card
from ...media.ffmpeg import video_dims
from .. import framebase, manual
from ..context import AppContext
from ..job import FrameAsk, Scene, VideoJob
from .idea import LANG_NAMES

log = logging.getLogger(__name__)

# How many cards the model may rank per stretch. More than one is not a nicety: the
# same card may not appear twice in a row, so the first choice is regularly
# unavailable, and without fallbacks the stretch goes to an ask the base could have
# covered. Four is enough for any run of blocked choices that actually happens.
RANK_DEPTH = 4

MATCH_SYSTEM = (
    "You are the picture editor of a short vertical video narrated from inside a fictional "
    "world. The picture is not filmed: it is chosen from the world's own standing collection of "
    "stills, its FRAME BASE, and the same still is deliberately shown several times across a "
    "video and again in the next one. Your job has two halves — say which stills fit each "
    "stretch of narration, and ask for a new still only where the base genuinely has nothing.\n"
    "YOU GET the numbered stretches of this video — what is HEARD over each one, what the writer "
    "asked to be SHOWN, and who is named in it — and the base's cards, each a name, a description "
    "of what is in it, and the regions of it somebody has marked and labelled. Descriptions name "
    "people and places but never describe looks. Do not expect them to, and never judge a card by "
    "how anything in it looks.\n"
    "FOR EVERY STRETCH, rank the cards that would fit, best first, up to {depth}. Rank more than "
    "one whenever more than one would do: the video cannot always use your first choice — the "
    "same still may never appear twice in a row — and your second and third are what it falls "
    "back on. Copy card names EXACTLY as given.\n"
    "ALSO SAY, for every stretch, how well your FIRST choice fits:\n"
    '  • "exact" — it shows what is being talked about, in the place it is happening.\n'
    '  • "close" — it shows the right place or the right people but not both, and nothing in it '
    "contradicts what is heard.\n"
    '  • "loose" — it belongs to this world and this part of the story, but it is not about what '
    "is being said.\n"
    '  • "wrong" — showing it would tell the viewer something untrue. Use this freely: it is what '
    "asks for a new picture, and a wrong picture costs more than a missing one.\n"
    "Judge the FIT, not the mood. A card is not \"exact\" because it is atmospheric.\n"
    "WHERE TO LOOK. A card's regions are listed with it. When the narration is about one of them "
    "— a face, a thing named out loud, a place within the place — give its label in \"target\" and "
    "the camera will move onto it. This is the only way to reach a region that is not anybody: a "
    "hat left on a counter carries no name to match on, so if the words mention the hat and the "
    "picture has it marked, say so. Leave \"target\" empty when nothing in particular is meant.\n"
    "THEN ASK for what is missing. Every stretch whose best card is \"wrong\", and any you could "
    "not rank at all, needs a picture that does not exist yet. Group them: stretches that could "
    "share ONE still go in a single ask, stretches that could not go in separate asks. The same "
    "person in two different places is two asks. The same person in the same place is one.\n"
    "WRITE EACH ASK AS A PLACE, NOT A MOMENT. The picture is made once and then spent three or "
    "four times in this video and again in later ones, so ask for a wide view of a place or a "
    "situation with several distinct things in it worth looking at — that is what lets one still "
    "become several shots. An ask pinned to an instant of the story (\"she lets the cap fall\") "
    "buys a picture that can never be used again. Name the characters in it by name and never "
    "describe their looks: their appearance is added later from elsewhere, and anything you write "
    "about it here would contradict that.\n"
    "Each ask has:\n"
    '  • "prompt": ENGLISH, for a picture generator — the place, the light, what is in it, framed '
    "wide. Comma-led phrases; no story, no camera moves, no text in the image.\n"
    '  • "description": the same picture said plainly in {lang}, naming who and what is in it and '
    "where. This becomes the new card's own description, so write it the way the descriptions you "
    "were given are written: names, never looks.\n"
    '  • "shots": the stretch numbers this one picture would cover.\n'
    'Respond with JSON only: {{"shots": [{{"n": <number>, "cards": ["name", ...], '
    '"target": "...", "fit": "exact|close|loose|wrong"}}, ...], "asks": [{{"prompt": "...", '
    '"description": "...", "shots": [<number>, ...]}}, ...]}}. Every stretch appears once in '
    '"shots". "asks" may be empty.'
)


def _cards_block(cards: list[FrameCard]) -> str:
    if not cards:
        return "(the base is empty — everything has to be asked for)"
    out = []
    for c in cards:
        line = f"- {c.name}: {c.description.strip() or '(no description written yet)'}"
        labels = [t.label.strip() for t in c.targets if t.label.strip()]
        if labels:
            line += f"\n    regions: {', '.join(labels)}"
        out.append(line)
    return "\n".join(out)


def _shots_block(job: VideoJob) -> str:
    out = []
    for i, s in enumerate(job.frame_shots, start=1):
        named = ", ".join(s.referents) or "—"
        out.append(
            f"{i}. [{s.start:.1f}s, {s.start + s.duration:.1f}s] named: {named}\n"
            f"   heard: {s.said.strip()[:400] or '—'}\n"
            f"   to show: {s.prompt.strip()[:300] or '—'}"
        )
    return "\n".join(out)


def _match(ctx: AppContext, job: VideoJob, cards: list[FrameCard]) -> tuple[
        dict[int, list[str]], dict[int, str], dict[int, str], list[dict]]:
    """One call over the whole video: what fits where, and what is missing."""
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    user = (
        f"Frame base ({len(cards)} cards):\n{_cards_block(cards)}\n\n"
        f"The video, {len(job.frame_shots)} stretches:\n{_shots_block(job)}"
    )
    data = ctx.llm.complete_json(
        "frame_match", MATCH_SYSTEM.format(depth=RANK_DEPTH, lang=lang), user)

    known = {c.name for c in cards}
    ranked: dict[int, list[str]] = {}
    targets: dict[int, str] = {}
    fits: dict[int, str] = {}
    for row in data.get("shots", []):
        if not isinstance(row, dict):
            continue
        try:
            i = int(row.get("n", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= i < len(job.frame_shots):
            continue
        # a name the base does not have is a hallucination, not a card
        ranked[i] = [n for n in row.get("cards", []) if isinstance(n, str) and n in known][:RANK_DEPTH]
        targets[i] = str(row.get("target", "")).strip()
        fits[i] = str(row.get("fit", "")).strip().lower()
    asks = [a for a in data.get("asks", []) if isinstance(a, dict)]
    return ranked, targets, fits, asks


def _fix_durations(job: VideoJob) -> None:
    """A still has no length of its own, so the scene simply spans its narration.

    This is the photo half of `drama_footage._sync`, done for the whole job up front
    because the picture track cannot be laid out one scene at a time."""
    for scene in job.scenes:
        if scene.is_ad:
            continue
        scene.audio_tempo = 1.0
        scene.video_tempo = 1.0
        if scene.audio_src_duration:
            scene.duration = scene.audio_src_duration


def _min_scales(cards: list[FrameCard], ctx: AppContext) -> dict[str, float]:
    """How far each card may be cropped into before it is being enlarged rather than
    framed. A 0.4 crop of a picture only as wide as the video is a window blown up
    two and a half times, and it shows — which is why an ask is written for twice the
    video's size."""
    out: dict[str, float] = {}
    for c in cards:
        path = c.path
        if path is None or not path.is_file():
            continue
        try:
            w, _ = video_dims(path)
        except Exception:  # a card that cannot be probed simply gets the plain floor
            continue
        if w > 0:
            out[c.name] = min(1.0, max(0.1, ctx.g.video.width / w))
    return out


def run(job: VideoJob, ctx: AppContext) -> None:
    """Plan the picture track, and record what the base could not cover."""
    if not framebase.active(job, ctx):
        return
    world = ctx.store.fandoms.get(ctx.params.fandom)
    cards = [c for c in (world.frames if world else []) if c.usable]
    _fix_durations(job)

    if job.frame_shots:
        # a re-run: the cuts were already agreed, and re-voicing a line only moves the
        # clock under them (see framebase.reanchor). Pins are kept; the rest is redone.
        framebase.reanchor(job.scenes, job.frame_shots)
        for s in job.frame_shots:
            if not s.pinned:
                s.card, s.move, s.target, s.fit = "", None, "", ""
    else:
        job.frame_shots = framebase.plan_cuts(job.scenes, ctx.params.cut_sensitivity)

    ranked, targets, fits, asks = ({}, {}, {}, [])
    if job.frame_shots:
        ranked, targets, fits, asks = _match(ctx, job, cards)

    accepts = FIT_ACCEPTS.get(ctx.params.frame_fit, FIT_ACCEPTS["close"])
    for i in list(ranked):
        if fits.get(i, "wrong") not in accepts:
            ranked[i] = []  # good enough for the model, not good enough for the operator
    if ctx.params.frame_fit == "any":
        # "any" is the stop-asking-me position, not a quality setting: a stretch the
        # model ranked nothing for still has to show something, so the whole base
        # becomes the ranking and the rhythm rules choose out of it. Without this the
        # setting quietly still asks, which is the one thing it promises not to do.
        every = [c.name for c in cards]
        for i in range(len(job.frame_shots)):
            if not ranked.get(i):
                ranked[i] = every

    stale = frozenset(c.name for c in cards if card_is_stale(c))
    framebase.assign(job.frame_shots, cards, ranked, targets=targets,
                     min_scales=_min_scales(cards, ctx), stale=stale,
                     seed=f"{job.index}|{job.topic}")
    for i, s in enumerate(job.frame_shots):
        s.fit = fits.get(i, "")
        s.target = targets.get(i, "")
    job.frame_shots = _fuse(job.frame_shots, cards, _min_scales(cards, ctx), stale, job)

    job.frame_asks = _asks_for(job, asks)
    covered = sum(1 for s in job.frame_shots if s.card)
    log.info("picture: %d shots, %d covered by the base, %d pictures to ask for",
             len(job.frame_shots), covered, len(job.frame_asks))


def _asks_for(job: VideoJob, asks: list[dict]) -> list[FrameAsk]:
    """Turn the model's grouping into asks, over the shots that really are uncovered.

    The model grouped by meaning, before the rhythm rules had their say, so a stretch
    it wanted a picture for may have ended up covered anyway — and one it thought
    covered may have been blocked. What is actually missing is only knowable here."""
    missing = {i for i, s in enumerate(job.frame_shots) if not s.card}
    out: list[FrameAsk] = []
    claimed: set[int] = set()
    for a in asks:
        idx = set()
        for n in a.get("shots", []):
            try:
                i = int(n) - 1
            except (TypeError, ValueError):
                continue
            if i in missing and i not in claimed:
                idx.add(i)
        if not idx:
            continue
        claimed |= idx
        out.append(FrameAsk(
            id=f"frame_{len(out):02d}",
            prompt=str(a.get("prompt", "")).strip(),
            description=str(a.get("description", "")).strip(),
            shots=sorted(idx),
        ))
    # anything uncovered the model said nothing about still has to be asked for, one
    # picture each: no grouping is safer than a guessed one
    for i in sorted(missing - claimed):
        s = job.frame_shots[i]
        out.append(FrameAsk(id=f"frame_{len(out):02d}", prompt=s.prompt, shots=[i]))
    for a in out:
        for i in a.shots:
            job.frame_shots[i].ask_id = a.id
    return out


# --------------------------------------------------------------------------
# what the base could not cover: asking for it, and taking it in
# --------------------------------------------------------------------------

# A card is cropped INTO, so it has to be bought bigger than the video. Asking for
# 1080x1920 and then framing half of it is how a base ends up soft.
ASK_SCALE = 2

FRAME_BRIEF = (
    "wide composition with several distinct focal regions, sharp throughout, "
    "no motion blur, no text, no watermark"
)


def _slug(text: str, taken: set[str]) -> str:
    """A filename for a new card: readable, and the world's own alphabet is fine —
    every other config here is already named in it."""
    base = re.sub(r"[^\w \-]", "", text).strip()[:40].strip() or "кадр"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def ask_note(ask_id: str) -> str:
    """What a card filed against an ask writes in its note — and the mark that says it
    was filed by slopgen rather than put in the base by hand, which is what lets a
    delivery be taken back without retiring a picture somebody chose themselves."""
    return f"доставлено по запросу {ask_id}"


def card_for_file(world, delivered: Path) -> FrameCard | None:
    """The card this exact picture is ALREADY filed as, matched on its checksum.

    A picture reaches the base by two roads now — the browser files it the moment it is
    handed over, so there is something to draw crop regions ON while the operator is
    still sitting there, and the resume files whatever arrived any other way. Both call
    :func:`file_card`, and without this they would file the same file twice: a duplicate
    card, and the run using the copy nobody marked up. The checksum is the honest
    identity here — the card already carries one for exactly this kind of question (see
    `config.models.FrameCard`), and a name cannot be trusted because `_slug` invents a
    new one on a collision."""
    p = Path(delivered)
    if world is None or not p.is_file():
        return None
    sha = file_sha(p)
    return next((c for c in world.frames if c.file_sha and c.file_sha == sha), None)


def file_card(world, asked: FrameAsk, delivered: Path) -> FrameCard:
    """Take one delivered picture into the world's frame base, and return its card.

    The card carries no crop targets: nobody has marked this picture up yet, so it can
    only be held, pushed into or drifted across until somebody does — which is honest,
    and still looks like the source footage.

    It is also written into the loaded world in memory, not only to disk. `ConfigStore`
    is built once per run, so a card that reached the folder and not the list would be
    invisible until the next process — and the whole point of delivering it now is that
    this run uses it."""
    already = card_for_file(world, delivered)
    if already is not None:
        # somebody has taken this exact picture in already — the browser does it at the
        # moment of delivery — and filing it again would cost the markup drawn since
        if already.retired:
            # a retired card being handed over as an answer is a card wanted back
            already.retired = False
            write_frame_card(already)
        return already
    root = frames_dir(world)
    root.mkdir(parents=True, exist_ok=True)
    name = _slug(asked.description or asked.prompt, {c.name for c in world.frames})
    dest = root / f"{name}{delivered.suffix.lower()}"
    shutil.copy2(delivered, dest)
    card = FrameCard(
        name=name, file=dest.name, prompt=asked.prompt, description=asked.description,
        note=ask_note(asked.id), file_sha=file_sha(dest), root=root,
    )
    write_frame_card(card)
    world.frames.append(card)
    return card


def pin_card(job: VideoJob, ask: FrameAsk, card: FrameCard,
             rng: random.Random | None = None) -> None:
    """Answer one ask with a card, and lay it onto every shot the ask covers.

    There are two ways an ask gets answered and this is the half they share. One is a
    picture the operator makes and hands over, which becomes a card first
    (:func:`file_card`) and arrives here second. The other is a card the base ALREADY
    holds, chosen by the operator in the browser because the matcher was wrong about it
    — a picture is not a paragraph, and the model reading a card's description was never
    going to outvote somebody looking at the thing. Either way what happens next is
    identical: the ask records which card it became, and its shots are pinned to it.

    `pinned` is what protects the choice from being made again. It says the operator
    decided this one, and `_fuse` may not overrule it on the next pass — which matters
    here more than on a delivery, since the whole point of picking from the base is that
    it is a picture the automatic pass had already declined to use."""
    rng = rng or random.Random(f"{job.index}|{ask.id}|{card.name}")
    ask.card = card.name
    for i in ask.shots:
        s = job.frame_shots[i]
        s.card, s.pinned = card.name, True
        s.move = framebase.move_for(card, s.referents, s.duration, "", rng)


def unpin_card(job: VideoJob, ask: FrameAsk) -> None:
    """Undo :func:`pin_card` — the ask is owed a picture again.

    Every shot it covers goes back to uncovered rather than to whatever the matcher had
    thought, because it had thought nothing: an ask exists precisely for the stretches
    no card could be found for (see :func:`_asks_for`), so "" is where these came from."""
    ask.card = ""
    for i in ask.shots:
        s = job.frame_shots[i]
        s.card, s.pinned, s.move = "", False, None


def collect(job: VideoJob, ctx: AppContext) -> None:
    """Ask for what the base is missing, take in what has arrived, and lay the track
    onto the scenes.

    Everything the video needs is asked for in ONE pause. The manifest, the inbox, the
    `ManualInputPending` checkpoint and the gather screen are all :mod:`..manual`'s,
    unchanged — a frame ask is just a third kind of id alongside `shot_NN` and
    `fg_NN_K`, and `manual._match_shot` already matches an exact id, so `frame_00.png`
    in the inbox needs no new plumbing at all.

    A delivered picture goes straight onto the shots that asked for it. There is no
    second trip to the matcher: the operator made this picture FOR those stretches, and
    re-deciding where it belongs would be both a waste and a way to put it somewhere
    else."""
    world = ctx.store.fandoms.get(ctx.params.fandom)
    cards = [c for c in (world.frames if world else []) if c.usable]
    pending = [a for a in job.frame_asks if not a.card]

    if pending and world is not None:
        specs = [
            manual.ShotSpec(
                id=a.id,
                prompt=", ".join(x for x in (
                    shot_prompt_for(job, ctx, a), FRAME_BRIEF, ctx.style_suffix) if x),
                target_s=max((job.frame_shots[i].duration for i in a.shots), default=5.0),
                scene_index=0, part=1, kind="generate", want="photo",
            )
            for a in pending
        ]
        delivered = manual.collect_or_pause(
            job.workdir, specs,
            ctx.g.video.width * ASK_SCALE, ctx.g.video.height * ASK_SCALE)
        rng = random.Random(f"{job.index}|delivered")
        for a in pending:
            path = delivered.get(a.id)
            if path is None:
                continue
            card = file_card(world, a, path)
            cards.append(card)
            pin_card(job, a, card, rng)

    job.frame_shots = _fuse(job.frame_shots, cards, {}, frozenset(), job)
    framebase.apply_to_scenes(job, cards)
    job.pending_parts = []


def shot_prompt_for(job: VideoJob, ctx: AppContext, ask: FrameAsk) -> str:
    """The ask, with every character in it swapped for their compiled look.

    The matcher wrote the ask naming people and nothing else, exactly as a shot prompt
    is written, so the same substitution the rest of the pipeline uses applies here —
    which is why the cast is still compiled in a mode that registers no entities."""
    from .drama_footage import shot_prompt

    text = ask.prompt or " ".join(job.frame_shots[i].prompt for i in ask.shots[:1])
    stub = Scene(text="", video_prompt=text,
                 characters=sorted({r for i in ask.shots for r in job.frame_shots[i].referents}))
    return shot_prompt(stub, job.cast_prompts, ctx.params.visual_notes, {})


def _fuse(shots, cards, scales, stale, job):
    """Merge neighbouring shots showing the same picture, and re-move what merged.

    A fused shot is longer than either half was, so the move it carried no longer fits
    it — `merge_adjacent` drops it and this is where the replacement comes from. The
    previous shot's kind is threaded through so the anti-repetition rule survives the
    merge, which it did not in the first live run: the delivery path passed no history
    at all and two push-ins ran back to back."""
    by = {c.name: c for c in cards}
    fused = framebase.merge_adjacent(shots)
    rng = random.Random(f"{job.index}|fuse")
    last = ""
    for s in fused:
        card = by.get(s.card)
        if card is None:
            continue
        if s.move is None:
            s.move = framebase.move_for(card, s.referents, s.duration, last, rng,
                                        min_scale=scales.get(s.card, 0.1),
                                        stale=s.card in stale, target=s.target)
        last = s.move.kind
    return fused
