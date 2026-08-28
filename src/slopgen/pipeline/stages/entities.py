"""Drama stage 2: build the visual registry, then make the shot prompts use it.

The cast pins how the people the operator wrote down must look. Nothing pins
anything else, and a story reuses far more than its cast: a transforming
robot-house, one particular car, the kitchen everything happens in, a nameless
soldier who keeps turning up, a crowd carrying home-made placards. Named once here
and once there, each shot is drawn from scratch — which is why a "robot-house"
comes back as an ordinary robot and the same room is a different room every time.

So this stage does two passes over what the writer produced:

  1. **The registry** (one call, seeing every shot prompt at once — the only way to
     honestly tell what recurs). Whatever appears in more than one shot and is not
     already cast becomes an :class:`~..job.Entity` with a compiled English
     descriptor. It is deliberately untyped: the model decides what is worth
     pinning, and `kind` is a label for the operator to read, not a schema.
  2. **The rewrite** (windowed, like the writer's own pass). Each shot prompt is
     re-emitted so that it names its entities by their registry name, names every
     cast member the writer listed as present, and says where the shot happens.
     None of that changes the shot — the action, framing and mood stay put; it only
     stops the prompt from being un-substitutable or placeless.

Both are what the operator reviews at the ``entities`` breakpoint, which is the
last point where a look can be fixed for every shot at once instead of one prompt
at a time.
"""

from __future__ import annotations

import logging

from ..context import AppContext
from ..drama import plan_windows
from ..job import Entity, VideoJob

log = logging.getLogger(__name__)

REGISTRY_SYSTEM = (
    "You are the continuity supervisor for an AI-generated vertical video. You get every shot "
    "prompt of one video, numbered, plus the cast whose looks are already pinned elsewhere.\n"
    "Find the things these shots REUSE and that nothing else pins down, and describe each one "
    "once so every shot showing it can be drawn the same way. Register a thing when it appears "
    "in MORE THAN ONE shot and is not one of the cast.\n"
    "There is no list of allowed sorts of thing — judge by whether the video would look wrong if "
    "it changed between shots. That routinely means: a vehicle, machine or structure; a building "
    "or room the story keeps returning to; a distinctive prop; a recurring person who is not in "
    "the cast; even a crowd, when it is a specific one (uniforms, placards, matching outfits) "
    "rather than ordinary passers-by.\n"
    "Do NOT register: anything already in the cast; generic scenery that carries no identity "
    "('a room', 'the sky', 'trees', 'a street'); or a thing shown in only one shot — it has "
    "nothing to stay consistent WITH.\n"
    'For each one give:\n'
    '  • "name": the EXACT string the prompts already use for it (copy it verbatim, spelling and '
    "all). This is what gets substituted, so it must match the prompts. If the prompts call it "
    "several things, pick the clearest and list the rest in \"aliases\".\n"
    '  • "aliases": other strings the prompts use for the same thing ([] if none).\n'
    '  • "kind": a short free-form label for a human reader (e.g. "machine", "location", '
    '"vehicle", "prop", "crowd"). It is cosmetic — nothing depends on it.\n'
    '  • "note": one short sentence in {lang} saying what it is, for the operator reviewing this list.\n'
    '  • "visual_prompt": a token-dense, comma-separated ENGLISH tag descriptor for txt2img/txt2vid '
    "— what it LOOKS like, concrete and reusable across shots (materials, scale, colour, shape, "
    "distinctive features, state). No sentences, no camera directions, no story. Where the prompts "
    "use a made-up compound a generator cannot know ('robot-house'), this descriptor is what "
    "teaches it: say plainly that it is a house that has become a mech — walls and windows as "
    "armour, roof hardware, legs unfolded from the foundation — so it never renders as a plain robot.\n"
    'Respond with JSON only: {{"entities": [{{"name": "...", "aliases": [], "kind": "...", '
    '"note": "...", "visual_prompt": "..."}}, ...]}}. An empty list is a valid answer.'
)

REWRITE_SYSTEM = (
    "You are fixing the shot prompts of an AI-generated vertical drama so they can actually be "
    "rendered. You get numbered prompts; return the SAME shots, corrected. Never change what "
    "happens: the action, the framing, the mood and the number of shots all stay exactly as they "
    "are. You are only making three things explicit.\n"
    "1. NAME THE REGISTERED THINGS. The registry below lists things whose look is pinned "
    "elsewhere; slopgen swaps each name for its full description before the prompt reaches the "
    "generator. So a shot showing one MUST call it by its registry name, spelled exactly. Replace "
    "any paraphrase with the registered name ('one giant robot' → the registered name of the "
    "machine it actually is) and never describe its look yourself.\n"
    "2. NAME THE PEOPLE PRESENT. Each shot lists the cast members in it. Every one of those names "
    "must appear in the prompt, spelled exactly, doing something — the same substitution applies "
    "to them. A shot that says 'a man' or 'both men' cannot be substituted into, and the look is "
    "then dumped at the end of the prompt where it binds to nobody. Do not describe anyone's "
    "appearance yourself; the name is enough.\n"
    "3. SAY WHERE IT HAPPENS. Every prompt must state the setting and the vantage — indoors or "
    "out, what surrounds the action, and, when the story puts it there, the part that makes the "
    "shot legible at all (hanging OUT of a broken window, high above the ground, on the wing of a "
    "flying jet). A prompt that only lists people and a verb gets rendered as those people "
    "standing in an empty room, which is the single most common way these shots fail.\n"
    "Keep every prompt ONE continuous shot in one or two sentences: one camera, one unbroken "
    "action. Never a list of moments, no 'THEN', no cuts, montage, sequence, split screen or "
    "storyboard — a generator renders those as every shot on screen at once. Keep it token-dense "
    "and English throughout.\n"
    # NOTE: this one is used verbatim, never .format()ed — single braces on purpose
    'Respond with JSON only: {"shots": [{"n": <the shot number you were given>, '
    '"video_prompt": "..."}, ...]}. Return every shot you were given, once each.'
)


def _registry(ctx: AppContext, job: VideoJob, lang: str) -> list[Entity]:
    """One call over every shot prompt: what recurs, and what it looks like."""
    shots = "\n".join(
        f"{i + 1}. {s.video_prompt}" for i, s in enumerate(job.scenes) if s.video_prompt.strip()
    )
    if not shots:
        return []
    cast = ", ".join(job.cast_prompts) or "(none)"
    user = (
        f"Cast already pinned (never register these): {cast}\n\n"
        f"Shot prompts ({len(job.scenes)} shots):\n{shots}"
    )
    data = ctx.llm.complete_json("drama_entities", REGISTRY_SYSTEM.format(lang=lang), user)

    out: list[Entity] = []
    seen = {n.casefold() for n in job.cast_prompts}
    for row in data.get("entities", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        look = str(row.get("visual_prompt", "")).strip()
        # a nameless entity cannot be substituted on, and one with no look pins nothing
        if not name or not look or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(Entity(
            name=name,
            kind=str(row.get("kind", "")).strip(),
            note=str(row.get("note", "")).strip(),
            visual_prompt=look,
        ))
        # aliases share the look, so a prompt that used the other wording still binds
        for alias in row.get("aliases", []) if isinstance(row.get("aliases"), list) else []:
            a = str(alias).strip()
            if a and a.casefold() not in seen:
                seen.add(a.casefold())
                out.append(Entity(name=a, kind=str(row.get("kind", "")).strip(),
                                  note=f"= {name}", visual_prompt=look))
    return out


def unnamed_cast(scene) -> list[str]:
    """Cast members the writer listed as present but never named in the prompt.
    Their look cannot be substituted in place, so footage appends it as a trailing
    bag bound to nobody — the shot then tends to come back as people standing about."""
    prompt = (scene.video_prompt or "").casefold()
    return [c for c in scene.characters if c.strip() and c.casefold() not in prompt]


def _rewrite(ctx: AppContext, job: VideoJob, entities: list[Entity]) -> int:
    """Re-emit the shot prompts, a window at a time, so each names its entities and
    the people in it and says where it happens. Returns how many actually changed."""
    registry = "\n".join(f"- {e.name} ({e.kind or 'thing'}): {e.note or e.visual_prompt}"
                         for e in entities) or "(nothing registered)"
    changed = 0
    windows = plan_windows(len(job.scenes))
    for wi, (first, last) in enumerate(windows):
        lines = []
        for i in range(first, last):
            s = job.scenes[i]
            present = ", ".join(s.characters) or "(nobody)"
            missing = unnamed_cast(s)
            gap = f" — NOT NAMED in the prompt yet: {', '.join(missing)}" if missing else ""
            lines.append(f"{i + 1}. [people in shot: {present}{gap}]\n   {s.video_prompt}")
        user = (
            f"Registered things (call each by this exact name when its shot shows it):\n{registry}\n\n"
            f"Cast names (spell them exactly): {', '.join(job.cast_prompts) or '(none)'}\n\n"
            f"Shots {first + 1}-{last}:\n" + "\n".join(lines)
        )
        data = ctx.llm.complete_json("drama_shot_fix", REWRITE_SYSTEM, user)
        for row in data.get("shots", []):
            if not isinstance(row, dict):
                continue
            try:
                n = int(row.get("n", 0)) - 1
            except (TypeError, ValueError):
                continue
            prompt = str(row.get("video_prompt", "")).strip()
            # only accept a shot this window actually owns — a model that renumbers
            # must not be allowed to overwrite a neighbour's prompt
            if not (first <= n < last) or not prompt:
                continue
            if prompt != job.scenes[n].video_prompt:
                job.scenes[n].video_prompt = prompt
                changed += 1
        ctx.progress("entities", wi + 1, len(windows))
    return changed


def run(job: VideoJob, ctx: AppContext) -> None:
    # anyone the operator wrote into a shot at the `script` breakpoint still needs a
    # look, and this is the first stage after it (see `beats.ensure_cast_prompts`)
    from .beats import ensure_cast_prompts

    ensure_cast_prompts(job, ctx)
    # nothing to pin and nothing to fix when no shot was described in the first place
    # (an all-ad or fully hand-authored script) — both passes would just burn calls
    if not any(s.video_prompt.strip() for s in job.scenes):
        return
    lang = ctx.params.lang
    job.entities = _registry(ctx, job, lang)
    log.info(
        "visual registry: %d entities (%s)",
        len(job.entities), ", ".join(e.name for e in job.entities) or "none",
    )
    changed = _rewrite(ctx, job, job.entities)
    log.info("shot prompts rewritten: %d/%d", changed, len(job.scenes))

    # whatever the rewrite could not fix stays visible to the operator: these shots
    # will have their character looks appended as a trailing bag instead of bound in
    # place, and the `entities` breakpoint is where that gets corrected by hand.
    still = [i + 1 for i, s in enumerate(job.scenes) if unnamed_cast(s)]
    if still:
        log.warning(
            "%d shot(s) still name nobody they list as present (shots %s). Their looks will be "
            "appended loose rather than bound to the person acting — fix them at the `entities` "
            "or `script` breakpoint.",
            len(still), ", ".join(str(n) for n in still),
        )
