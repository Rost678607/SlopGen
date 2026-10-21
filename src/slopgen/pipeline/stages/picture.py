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

Or the model is not asked at all. With `RunParams.frame_by_hand` the stage does the
first half and stops: the cuts are laid out of the speech, every shot is left empty,
nothing is asked for, and the run parks on this stage's breakpoint with a track for the
operator to fill in by hand (see :mod:`..montage`). It is the same two judgements in the
same order — where to cut, then what to show — with the second one made by somebody
looking at the pictures instead of by somebody reading about them.
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
from .. import effects
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
    "{sources}"
    "{machinima}"
    'Respond with JSON only: {{"shots": [{{"n": <number>, "cards": ["name", ...], '
    '"target": "...", "fit": "exact|close|loose|wrong"}}, ...], "asks": [{{"prompt": "...", '
    '"description": "...", {ask_fields}"shots": [<number>, ...]}}, ...]{machinima_field}}}. Every stretch '
    'appears once in "shots". "asks" may be empty.'
)

# Offered when `minecraft` is ticked and the world is built. Unlike every other
# source this one is asked for a MOMENT rather than a place: the stretches are played
# under their own words, so the piece is about this video and nothing else.
MACHINIMA_NOTE = (
    "PLAY IT IN THE WORLD'S OWN BUILD. This world is built in Minecraft, and these places in it "
    "are marked up for staging:\n{places}\n"
    "Some stretches are better PLAYED there than shown as a still: actors who are this world's "
    "people (blocky figures — no faces, no fine hand work) do what the narration says, in one of "
    "these places, filmed under those very words. That is a MOMENT, not a place: a run of "
    "CONSECUTIVE stretches whose narration has people doing something — walking in, turning to "
    "each other, talking, sitting down, a crowd at the counter. Never a place that is not listed. "
    "List them in \"machinima\": each with the stretch numbers it covers (consecutive), the place, "
    "and in {lang} what is happening. Stretches you put there need no card and no ask; still rank "
    "cards for them in \"shots\", in case the playing fails.\n"
)


def _machinima_places(ctx) -> str:
    """The marked-up places of the fandom's Minecraft build, as the matcher reads them,
    or "" when the source is not ticked or the world is not built — then the section is
    left out and the field with it."""
    if "minecraft" not in framebase.sources(ctx.params):
        return ""
    fandom = ctx.store.fandoms.get(ctx.params.fandom)
    mc = getattr(fandom, "minecraft", None)
    if mc is None or not mc.world:
        return ""
    try:
        from ...minecraft import paths
        from ...minecraft.registry import Registry

        reg = Registry.load(paths.save_dir(mc))
    except Exception as e:  # an unreadable markup is a machinima-less run, not a dead one
        log.warning("picture: the Minecraft markup cannot be read — %s", e)
        return ""
    lines = [f"  - «{p.name}»" + (f": {p.description}" if p.description else "") for p in reg.places]
    return "\n".join(lines)

# What each source that can fill an ask IS, said to the matcher in the terms that
# decide between them: what it is good at and what it costs. Only the ticked ones are
# offered, and with one ticked there is nothing to decide and the section is left out
# altogether — a choice of one is a field the model can only get wrong.
SOURCE_NOTES = {
    "ai_photo": "an automatic picture generator draws the still. Immediate and free. Good "
                "at places, light, crowds, objects and weather; weak at a precise action, "
                "at hands, and at anything that has to be one particular face.",
    "ai_video": "an automatic video generator makes a clip of about five seconds, looped "
                "to fill its stretches. Only worth it where something MOVES and the "
                "movement is the point — smoke, water, a crowd going past, a machine "
                "running. Slow, and poor at faces and at anything precise.",
    "manual": "the operator makes it by hand in a stronger outside generator from your "
              "prompt. It costs a person's time, so spend it where the automatic ones would "
              "get it wrong: a named person doing a particular thing, a composition that "
              "has to be exact. Also say \"want\": \"photo\" or \"video\".",
    "search": "the operator FINDS a real photograph or clip on a stock site. Only for what "
              "a camera in our own world could have filmed — weather, a street, hands at "
              "work, an ordinary object, a texture. Never for anything only this world "
              "has: it does not exist to be found.",
}


def _sources_block(allowed: list[str]) -> str:
    if len(allowed) < 2:
        return ""
    lines = "\n".join(f'  • "{s}" — {SOURCE_NOTES[s]}' for s in allowed)
    return (
        "WHO MAKES EACH ASK. Every ask names, in \"source\", which of these fills it:\n"
        f"{lines}\n"
        "Choose per ask, by what the picture needs; one video may use all of them.\n"
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
        dict[int, list[str]], dict[int, str], dict[int, str], list[dict], list[dict]]:
    """One call over the whole video: what fits where, what is missing — and, where
    more than one source is ticked, which of them each missing picture comes from."""
    lang = LANG_NAMES.get(ctx.params.lang, ctx.params.lang)
    allowed = framebase.ask_sources(ctx.params)
    choose = len(allowed) > 1
    user = (
        f"Frame base ({len(cards)} cards):\n{_cards_block(cards)}\n\n"
        f"The video, {len(job.frame_shots)} stretches:\n{_shots_block(job)}"
    )
    fields = ('"source": "' + "|".join(allowed) + '", '
              + ('"want": "photo|video", ' if "manual" in allowed else "")) if choose else ""
    places = _machinima_places(ctx)
    data = ctx.llm.complete_json(
        "frame_match", MATCH_SYSTEM.format(
            depth=RANK_DEPTH, lang=lang, sources=_sources_block(allowed),
            ask_fields=fields,
            machinima=MACHINIMA_NOTE.format(places=places, lang=lang) if places else "",
            machinima_field=(', "machinima": [{"shots": [<number>, ...], "place": "...", "why": "..."}]'
                             if places else "")), user)

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
    machinima = [m for m in data.get("machinima", []) if isinstance(m, dict)] if places else []
    return ranked, targets, fits, asks, machinima


def fix_durations(job: VideoJob) -> None:
    """A still has no length of its own, so the scene simply spans its narration.

    Public because the montage room applies it after any stage it runs by hand: `tts`
    pressed on its own leaves the lengths unset, since in a beat mode this is the stage
    that settles them (see `..montage.settle`).

    This is the photo half of `drama_footage._sync`, done for the whole job up front
    because the picture track cannot be laid out one scene at a time."""
    for scene in job.scenes:
        if scene.is_ad:
            continue
        scene.audio_tempo = 1.0
        scene.video_tempo = 1.0
        if scene.audio_src_duration:
            scene.duration = scene.audio_src_duration


# The tightest crop a card is ever given, however small it is.
#
# The floor below is a QUALITY rule and it is right about quality: a window showing
# fewer source pixels than the output has is being enlarged, and a deep crop of a
# small picture shows it. What it had no answer for was the case where the rule
# forbids everything — a card no wider than the video gets a floor of 1.0, every move
# `move_for` can build comes out with a == b, and a push-in, a zoom and a pan all
# render as a frozen frame with nothing anywhere saying why. Found on a base of
# screenshots somebody had marked regions on and chosen zooms for, none of which did
# anything.
#
# A slightly soft move is a worse picture; a frozen one is a broken feature. So the
# rule keeps governing wherever the card is big enough to be governed, and stops short
# of forbidding movement outright: at 0.8 the crop adds a quarter again of
# enlargement, which on a phone is nothing next to what the fit to a portrait frame
# has already done to a landscape screenshot.
SOFT_FLOOR = 0.8


def soft(card: FrameCard, cfg) -> bool:
    """Whether cropping into this card at all means enlarging it — the montage room
    says so, rather than leaving the operator to wonder why a zoom looks mushy."""
    try:
        w, _h = video_dims(card.path) if card.path else (0, 0)
    except Exception:
        return False
    return bool(w) and cfg.video.width / w > SOFT_FLOOR


def min_scales(cards: list[FrameCard], cfg) -> dict[str, float]:
    """How far each card may be cropped into: to the pixel where the window stops
    having as many source pixels as the output, and never tighter than
    :data:`SOFT_FLOOR` even when that rule would forbid cropping altogether.

    A big card keeps the whole benefit — a picture bought at twice the video's size
    (`ASK_SCALE`) is still croppable to half, which is what buys the deep move onto a
    marked region. A small one gets the floor and a warning rather than a freeze (see
    :func:`soft`).

    It takes the GLOBAL config rather than the run's context because the only thing it
    needs is the video's width, and the montage screen asks it while casting a card by
    hand — a question about geometry that must not require an LLM client to answer."""
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
            out[c.name] = max(0.1, min(SOFT_FLOOR, cfg.video.width / w))
    return out


def run(job: VideoJob, ctx: AppContext) -> None:
    """Plan the picture track, and record what the base could not cover."""
    if not framebase.active(job, ctx):
        return
    world = world_of(job, ctx)
    # Two lists, and they differ only on a run that did not tick the base. What the
    # MATCHER may spend is that run's own cards; what may be on the track is every card
    # the world has, because the operator may have cast one by hand in the montage room
    # and a pin is not this stage's to take back.
    every = [c for c in (world.frames if world else []) if c.usable]
    cards = framebase.matchable(world, ctx.params)
    fix_durations(job)

    if job.frame_shots:
        # a re-run: the cuts were already agreed, and re-voicing a line only moves the
        # clock under them (see framebase.reanchor). Pins are kept; the rest is redone.
        framebase.reanchor(job.scenes, job.frame_shots)
        for s in job.frame_shots:
            if not s.pinned:
                s.card, s.move, s.target, s.fit = "", None, "", ""
    else:
        job.frame_shots = framebase.plan_cuts(job.scenes, ctx.params.cut_sensitivity)

    if ctx.params.frame_by_hand:
        # Nobody is matching, so there is nothing to match against and nothing to ask
        # for: the track is the cuts and the operator fills it in at the breakpoint
        # (see pipeline/montage). Pins are the exception and always were — a shot the
        # operator has already cast on an earlier pass is their decision, and a re-run
        # of this stage is not a reason to take it back.
        job.frame_asks = []
        # …and so are the effects: choosing is the thing this switch turns off, and a
        # cue placed by hand is a choice already made (see `fx.settle`).
        effects.settle(job, every)
        log.info("picture: %d shots, cut by the speech and left for the operator",
                 len(job.frame_shots))
        return

    ranked, targets, fits, asks, played = ({}, {}, {}, [], [])
    if job.frame_shots:
        ranked, targets, fits, asks, played = _match(ctx, job, cards)
    if job.machinima:
        # a re-run: the blocks stay where their words are, and say if a re-voice moved
        # anything their actors play
        from ...minecraft import block as mc_block

        mc_block.measure(job)
        mc_block.mark_stale(job)
    if played:
        from ...minecraft import block as mc_block

        mc_block.claim(job, played)
    for i, s in enumerate(job.frame_shots):
        if s.block:
            ranked[i] = []  # a played stretch shows its block, not a card

    accepts = FIT_ACCEPTS.get(ctx.params.frame_fit, FIT_ACCEPTS["close"])
    for i in list(ranked):
        if fits.get(i, "wrong") not in accepts:
            ranked[i] = []  # good enough for the model, not good enough for the operator
    if ctx.params.frame_fit == "any":
        # "any" is the stop-asking-me position, not a quality setting: a stretch the
        # model ranked nothing for still has to show something, so the whole base
        # becomes the ranking and the rhythm rules choose out of it. Without this the
        # setting quietly still asks, which is the one thing it promises not to do.
        names = [c.name for c in cards]
        for i in range(len(job.frame_shots)):
            if not ranked.get(i):
                ranked[i] = names

    stale = frozenset(c.name for c in every if card_is_stale(c))
    scales = min_scales(every, ctx.g)
    framebase.assign(job.frame_shots, every, ranked, targets=targets,
                     min_scales=scales, stale=stale,
                     seed=f"{job.index}|{job.topic}")
    for i, s in enumerate(job.frame_shots):
        s.fit = fits.get(i, "")
        s.target = targets.get(i, "")
    job.frame_shots = _fuse(job.frame_shots, every, scales, stale, job)
    if job.machinima:
        from ...minecraft import block as mc_block

        mc_block.measure(job)

    job.frame_asks = _asks_for(job, asks, framebase.ask_sources(ctx.params))
    _effects(job, ctx, every)
    covered = sum(1 for s in job.frame_shots if s.card)
    log.info("picture: %d shots, %d covered by the base, %d played in %d machinima blocks, %d pictures to ask for",
             len(job.frame_shots), covered, sum(1 for s in job.frame_shots if s.block),
             len([b for b in job.machinima if not b.failed]), len(job.frame_asks))


def _effects(job: VideoJob, ctx: AppContext, cards: list[FrameCard]) -> None:
    """Fire the effects the cards have ready, onto the words they are about.

    Last, and after the fusing, because everything it reasons about is settled by then:
    which picture is up over which stretch, and therefore what that picture can do. Run
    any earlier it would be choosing effects for a card that the rhythm rules were
    still free to replace.

    It costs a model call and is skipped wherever that call could only answer
    "nothing" — a base with no effects in it, a world whose cards carry none, a run
    with the switch off. What is never skipped is the re-measuring: a cue the operator
    placed by hand survives every pass of this stage, and its seconds still have to
    follow the clock."""
    specs = dict(getattr(ctx.store, "effects", {}) or {})
    job.effect_cues = [q for q in job.effect_cues if q.pinned]
    effects.settle(job, cards)
    if not ctx.params.frame_effects or not effects.anything(cards, specs):
        return
    try:
        job.effect_cues = effects.plan(job, ctx, cards, specs)
    except Exception as e:  # an accent is not worth a run
        log.warning("effects: nothing fired — %s", e)
        return
    effects.settle(job, cards)
    log.info("picture: %d effects on the track", len(job.effect_cues))


def world_of(job: VideoJob, ctx: AppContext):
    """The world this job's pictures come out of, as the run sees it (see
    `framebase.run_world`). The run's folder is the job's parent: every video of a run
    shares the private base, the way they share the world's."""
    world = ctx.store.fandoms.get(ctx.params.fandom)
    return framebase.run_world(world, ctx.params, Path(job.workdir).parent)


def _source_of(a: dict, allowed: list[str]) -> tuple[str, str]:
    """Which ticked source the matcher gave this ask, and what it should come back as.

    A source that was not ticked is the model not listening, and it is overruled rather
    than obeyed: the boxes are the operator's. It falls to the first ticked one, which
    is as good a guess as any and better than a picture made somewhere it was told not
    to be made."""
    src = str(a.get("source", "")).strip().lower()
    if src not in allowed:
        src = allowed[0]
    want = {"ai_photo": "photo", "ai_video": "video"}.get(src, "")
    if src == "manual":
        want = "video" if str(a.get("want", "")).strip().lower() == "video" else "photo"
    return src, want


def _asks_for(job: VideoJob, asks: list[dict], allowed: list[str] | None = None) -> list[FrameAsk]:
    """Turn the model's grouping into asks, over the shots that really are uncovered.

    The model grouped by meaning, before the rhythm rules had their say, so a stretch
    it wanted a picture for may have ended up covered anyway — and one it thought
    covered may have been blocked. What is actually missing is only knowable here."""
    allowed = allowed or ["manual"]
    missing = {i for i, s in enumerate(job.frame_shots) if not s.card and not s.block}
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
        src, want = _source_of(a, allowed)
        out.append(FrameAsk(
            id=f"frame_{len(out):02d}",
            prompt=str(a.get("prompt", "")).strip(),
            description=str(a.get("description", "")).strip(),
            shots=sorted(idx), source=src, want=want,
        ))
    # anything uncovered the model said nothing about still has to be asked for, one
    # picture each: no grouping is safer than a guessed one
    src, want = _source_of({}, allowed)
    for i in sorted(missing - claimed):
        s = job.frame_shots[i]
        out.append(FrameAsk(id=f"frame_{len(out):02d}", prompt=s.prompt, shots=[i],
                            source=src, want=want))
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
    """Make or ask for what the base is missing, take in what has arrived, and lay the
    track onto the scenes.

    Each ask goes to the source the matcher gave it. The two automatic ones are called
    here and now, one picture per ask, and what they return is filed exactly like a
    delivery; one that fails becomes the operator's to make (`source` turns `manual`),
    because a stretch with no picture is not a video. Everything left for the operator
    is asked for in ONE pause. The manifest, the inbox, the `ManualInputPending`
    checkpoint and the gather screen are all :mod:`..manual`'s, unchanged — a frame ask
    is just a third kind of id alongside `shot_NN` and `fg_NN_K`, and
    `manual._match_shot` already matches an exact id, so `frame_00.png` in the inbox
    needs no new plumbing at all.

    A delivered picture goes straight onto the shots that asked for it. There is no
    second trip to the matcher: the operator made this picture FOR those stretches, and
    re-deciding where it belongs would be both a waste and a way to put it somewhere
    else."""
    world = world_of(job, ctx)
    cards = [c for c in (world.frames if world else []) if c.usable]
    if job.machinima:
        _film_blocks(job, ctx)
    pending = [a for a in job.frame_asks if not a.card]

    if pending and world is not None:
        rng = random.Random(f"{job.index}|delivered")
        for a in [a for a in pending if a.source in ("ai_photo", "ai_video")]:
            path = _generate(job, ctx, a)
            if path is None:
                log.warning("picture: %s could not be generated (%s) — it is yours to make",
                            a.id, a.source)
                a.source, a.want = "manual", "video" if a.source == "ai_video" else "photo"
                continue
            card = file_card(world, a, path)
            cards.append(card)
            pin_card(job, a, card, rng)
        pending = [a for a in pending if not a.card]

    if pending and world is not None:
        specs = [
            manual.ShotSpec(
                id=a.id,
                # a search is rewritten by the briefer below and starts from the shot;
                # a prompt for a person to paste says everything a generator is told
                prompt=shot_prompt_for(job, ctx, a) if a.source == "search" else ", ".join(
                    x for x in (shot_prompt_for(job, ctx, a), FRAME_BRIEF, ctx.style_suffix) if x),
                target_s=max((job.frame_shots[i].duration for i in a.shots), default=5.0),
                scene_index=0, part=1,
                kind="search" if a.source == "search" else "generate",
                # a search decides photo-or-clip in the briefer, per what the moment needs
                want="" if a.source == "search" else (a.want or "photo"),
            )
            for a in pending
        ]
        _brief(job, specs, ctx)
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
    # A picture delivered here moves the track under whatever was already on it, so the
    # cues are re-measured and the ones now pointing at a picture nobody can see are
    # dropped. Nothing new is fired: the model was asked once, when the plan was made.
    effects.settle(job, cards)
    framebase.apply_to_scenes(job, cards)
    job.pending_parts = []


def _film_blocks(job: VideoJob, ctx: AppContext) -> None:
    """Play and film every machinima block still short of its final clip. The game not
    being there pauses the run (see `minecraft.block.MachinimaWaiting`); a block that
    cannot be made gives its stretches back, and they are asked for like any other
    stretch the base does not cover."""
    from ...minecraft import block as mc_block

    mc_block.shoot(job, ctx, final=True)
    for b in job.machinima:
        if not b.failed and mc_block.clip(job, b) is None:
            mc_block.give_up(job, b, "no clip came out of the game")
    orphans = [i for i, s in enumerate(job.frame_shots) if not s.card and not s.block
               and not any(i in a.shots for a in job.frame_asks)]
    if not orphans:
        return
    src, want = _source_of({}, framebase.ask_sources(ctx.params))
    for i in orphans:
        s = job.frame_shots[i]
        ask = FrameAsk(id=f"frame_{len(job.frame_asks):02d}", prompt=s.prompt, shots=[i], source=src, want=want)
        job.frame_asks.append(ask)
        s.ask_id = ask.id


def _brief(job: VideoJob, specs: list[manual.ShotSpec], ctx: AppContext) -> None:
    """Turn the search asks into tasks a person can act on, as every other search is
    (see `drama_footage._brief_searches`) — once. The manifest is rebuilt from the specs
    on every resume, so a brief already written is carried over from it rather than
    paid for again, and only the searches it has never seen go to the briefer."""
    from .drama_footage import _brief_searches

    old = manual.ManualManifest.load(job.workdir)
    fresh = []
    for spec in specs:
        if spec.kind != "search":
            continue
        prev = old.by_id(spec.id)
        if prev and prev.kind == "search" and prev.queries:
            spec.prompt, spec.queries, spec.want = prev.prompt, list(prev.queries), prev.want
        else:
            fresh.append(spec)
    _brief_searches(fresh, ctx)


# What a generated CLIP is asked for besides what is in it. The still's brief is about
# cropping into it; a clip is not cropped (see `FrameCard`), so what matters instead is
# that it is one continuous shot that can be looped without a visible seam.
CLIP_BRIEF = "slow steady camera, one continuous shot, no cuts, no text, no watermark"


def _generate(job: VideoJob, ctx: AppContext, ask: FrameAsk) -> Path | None:
    """One picture for one ask, from the generator its source names, trying every key.

    A still is bought at `ASK_SCALE` for the reason a delivered one is: it is cropped
    into. A clip is whatever size the Space makes — it is looped, never cropped."""
    from dataclasses import replace

    from ...media.generate import env_keys, key_var_for_model, pollinations_image, wan_video
    from .drama_footage import SINGLE_FRAME, _genparams

    video = ask.source == "ai_video"
    model = ctx.params.fandom_video_model if video else ctx.params.fandom_photo_model
    brief = f"{CLIP_BRIEF}, {SINGLE_FRAME}" if video else FRAME_BRIEF
    prompt = ", ".join(x for x in (shot_prompt_for(job, ctx, ask), brief) if x)
    cache = ctx.g.paths.state / "cache" / ("footage" if video else "images")
    cache.mkdir(parents=True, exist_ok=True)
    for token in env_keys(key_var_for_model(model)) or [None]:
        gen = _genparams(ctx, model, token)
        if not video:
            gen = replace(gen, width=gen.width * ASK_SCALE, height=gen.height * ASK_SCALE)
        try:
            path = wan_video(prompt, cache, ctx.used_clips, gen) if video \
                else pollinations_image(prompt, cache, ctx.used_clips, gen)
        except Exception as e:
            log.info("picture: %s via %s failed — %s", ask.id, model, e)
            path = None
        if path:
            return Path(path)
    return None


def said_of(job: VideoJob, ask: FrameAsk) -> str:
    """The narration playing over the stretches this ask covers, once each.

    The honest source for what a missing picture is FOR. In a frame-base run it is
    also the only one left: the writer is no longer asked for shot descriptions (see
    `stages.fandom_script.FRAMES_RULE`), so `FrameShot.prompt` is empty, and a
    fallback that pointed at it produced an ask carrying nothing but the boilerplate
    suffix — which is what the operator saw. It is what the matcher reads to choose a
    card in the first place, so it is the right thing to hand back when the matcher
    picked none.

    Neighbouring stretches often carry the same line — one beat is cut into several
    pictures — so it is de-duplicated rather than repeated, in the order it plays."""
    out: list[str] = []
    for i in ask.shots:
        if 0 <= i < len(job.frame_shots):
            said = (job.frame_shots[i].said or "").strip()
            if said and said not in out:
                out.append(said)
    return " ".join(out)


def shot_prompt_for(job: VideoJob, ctx: AppContext, ask: FrameAsk) -> str:
    """The ask, with every character in it swapped for their compiled look.

    The matcher wrote the ask naming people and nothing else, exactly as a shot prompt
    is written, so the same substitution the rest of the pipeline uses applies here —
    which is why the cast is still compiled in a mode that registers no entities."""
    from .drama_footage import shot_prompt

    text = ask.prompt or said_of(job, ask)
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
