"""What the next videos are about, thought of before they are needed.

A run that is left without a topic invents one at the writing stage and gets on with it,
which is right for one video and wrong for a hundred: nobody ever sees the topic, so
nobody can say "not that one" until it is a finished video. This is the same question
asked EARLY — several answers at a time, into the loop's queue, where they sit as
ordinary entries to be read, rewritten, reordered or thrown away (see `pipeline/loop`).

The prompts are not new ones. Each mode already knows how to be asked what a video is
about — an info clip has `llm/topic`, a fandom has `llm/lore.write_brief`, a drama has
the story polish in `llm/characters` — and this dispatches to whichever the loop is
running rather than writing a fourth. A topic proposed here and a topic the run would
have invented for itself are therefore the same kind of sentence, which is the whole
reason to reuse them: the lookahead must change WHEN the operator sees the topic, never
what the videos are like.

De-duplication is the caller's, not the model's. `avoid` is handed in — what is already
queued, what this loop has already made — because a model asked for five topics in five
separate requests has no memory of the other four.
"""

from __future__ import annotations

import json
import logging

from ..config import ConfigStore, RunParams
from ..llm.client import ChatLLM

log = logging.getLogger(__name__)

# How many topics one request may be asked for. A loop with `ahead=20` is asking for a
# list, not for twenty sentences, and a model handed that much rope writes twenty
# variations of the first one. Past this the caller simply gets fewer and the next pass
# tops up again, which is also what keeps one slow request from holding up a video.
BATCH = 5


def next_topics(store: ConfigStore, params: RunParams, n: int = 1,
                avoid: list[str] | None = None) -> list[str]:
    """Propose `n` topics for a loop on these settings, avoiding the ones named.

    Returns what it got — fewer than asked for, or nothing at all, is a normal answer and
    never an error to the caller above: a loop must not stop because a model was in a
    mood (see `LoopRunner._stock`)."""
    want = max(1, min(int(n), BATCH))
    used = [t.strip() for t in (avoid or []) if str(t).strip()]
    llm = ChatLLM(store.active_llm_profile())
    if params.mode == "fandom":
        out = _fandom(store, llm, params, want, used)
    elif params.mode == "drama":
        out = _drama(store, llm, params, want, used)
    else:
        out = _info(store, llm, params, want, used)
    # the model has been told twice not to repeat itself and may still do it; the queue
    # is the last place that can refuse, and a duplicate here is a duplicate video
    seen = {t.casefold() for t in used}
    fresh: list[str] = []
    for t in out:
        key = t.strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        fresh.append(t.strip())
    return fresh[:want]


def proposer(store: ConfigStore):
    """The `propose` a `LoopRunner` takes: a plan and a number in, topics out.

    What to avoid is read off the plan itself — what is queued and what this loop has
    already made — so neither frontend has to remember to assemble it, and a loop steered
    from a terminal and one steered from the browser cannot end up avoiding different
    things."""

    def propose(plan, n: int) -> list[str]:
        avoid = [i.topic for i in plan.topics] + [it.topic for it in plan.iterations]
        return next_topics(store, plan.params, n, avoid)

    return propose


def _info(store: ConfigStore, llm, params: RunParams, n: int, avoid: list[str]) -> list[str]:
    """An info clip's topic, as `stages/idea` asks for it — with the history it reads.

    The history matters more here than anywhere else: this is the mode that makes
    hundreds of clips in one niche, and "the same fact again" is what a viewer notices
    first."""
    from ..llm.topic import write_topic

    ct = store.content_types.get(params.content_type)
    briefs = ct.idea_brief if ct else {}
    brief = briefs.get(params.lang) or next(iter(briefs.values()), "")
    recent = list(avoid) + _made(store, params)
    out: list[str] = []
    for _ in range(n):
        topic = write_topic(llm, params.lang, brief, recent + out)
        if not topic:
            break
        out.append(topic)
    return out


def _fandom(store: ConfigStore, llm, params: RunParams, n: int, avoid: list[str]) -> list[str]:
    """What to tell about this world next — one LINE each, not a brief.

    The one place this module does not simply reuse the mode's own "what is this video
    about" prompt, and the reason is worth writing down. `write_brief` is the wizard's ✨,
    and what the wizard is for is one video the operator is about to start: a brief with
    the evidence in it, two to five sentences, is exactly right there. A queue is the
    other thing. Asked for briefs, a loop filled its queue with paragraphs that were
    already most of a video — and the writer reads a long brief as the PIECE itself
    (`stages.fandom_script.BRIEF_RULE`), so those were not topics awaiting a video, they
    were videos nobody had agreed to. The queue is a list to be read, reordered and
    thrown out of, and a list is made of lines.

    A topic is also the only form the operator can correct cheaply. «Как попасть на
    Объект?» can be rewritten into what they meant in two seconds; a paragraph has to be
    read first, and by then it has already decided what the video is.

    The world's own records go in wherever they fit, for the reason the browser's brief
    endpoint says: the compiled sheet is one line per thing, and one line is where two
    similarly-named institutions stop being distinguishable."""
    from ..config.loader import read_lore
    from ..llm.lore import write_brief

    cfg = store.fandoms.get(params.fandom)
    if cfg is None:
        return []
    lore = read_lore(cfg)
    world = lore if lore and len(lore) <= 80_000 else ((cfg.canon or "").strip() or lore)
    if not world:
        return []
    out: list[str] = []
    for _ in range(n):
        brief = write_brief(llm, world, "", _dont_repeat(avoid + out), params.lang,
                            duration_s=params.duration_s, topic=True)
        if not brief:
            break
        out.append(brief)
    return out


def _drama(store: ConfigStore, llm, params: RunParams, n: int, avoid: list[str]) -> list[str]:
    """The next premise, out of the cast the loop is already carrying.

    Only the plot is taken. `autofill_all` may also want to add people or invent them,
    and a character created as a side effect of a loop topping up its queue overnight is
    a character nobody agreed to keep — the browser's story button refuses the same thing
    for the same reason."""
    from ..llm.characters import autofill_all

    cast = [{"name": c.name, "age": c.age, "appearance": c.appearance}
            for c in params.manual_cast]
    library = [{"name": c.name, "age": c.age, "appearance": c.appearance}
               for c in store.characters.values()]
    out: list[str] = []
    for _ in range(n):
        res = autofill_all(llm, cast, params.lang, "",
                           _dont_repeat(avoid + out), library=library)
        plot = str(res.get("scenario") or "").strip()
        if not plot:
            break
        out.append(plot)
    return out


def _dont_repeat(used: list[str]) -> str:
    """The instruction that carries the de-duplication into the two modes whose prompt
    takes one. `llm/topic` has a slot of its own for this; a brief and a premise are
    asked for in words, so the words are these."""
    ask = "Invent a fresh subject for the next video."
    if not used:
        return ask
    short = [t if len(t) <= 200 else t[:200] + "…" for t in used[-12:]]
    return (ask + " It must NOT repeat or paraphrase any of these, which have already "
            "been used:\n- " + "\n- ".join(short))


def _made(store: ConfigStore, params: RunParams) -> list[str]:
    """Topics this niche has already had, off the same history file the idea stage
    reads. Unreadable history is no history: it is a de-duplication aid, not a record
    anything depends on."""
    path = store.global_cfg.paths.state / "history.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))[-60:]
    except Exception:
        return []
    return [str(h.get("topic", "")) for h in rows
            if h.get("content_type") == params.content_type and h.get("lang") == params.lang]
