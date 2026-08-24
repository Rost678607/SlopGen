"""Fandom stage 0: make sure the world's canon sheet is current.

The compile itself lives in `llm/lore.py`; this stage is only the lazy guard in front
of it, and in the ordinary case it makes no LLM call at all. Saving lore in the TUI
already rebuilds the sheet, so by the time a run starts it is normally fresh — what
this stage catches is the operator who edited `lore.md` in their own editor, where
nothing in slopgen was watching. Hence a checksum rather than a dirty flag (see
`config.models.FandomConfig`).

A rebuilt sheet is written straight back into the fandom's `fandom.toml`, so the next
run — and the TUI, and the next fandom that reuses this world — gets it for free.
The sheet also rides along on the job, which is what makes a resumed run write
against the world as it stood when the script was started rather than as it stands
now: re-planning half a script against edited lore would contradict the half already
written.
"""

from __future__ import annotations

from ...config.loader import write_fandom
from ...llm.lore import recompile_if_stale
from ..context import AppContext
from ..job import VideoJob
from .idea import LANG_NAMES

# Below this much lore there is nothing to compile away: the documents are shorter
# than the sheet would be, so the writer simply reads them whole (see fandom_script)
# and the librarian tool has nothing to add either.
SMALL_LORE_CHARS = 4000


def run(job: VideoJob, ctx: AppContext) -> None:
    fandom = ctx.fandom
    if not fandom:
        if not ctx.params.fandom:
            raise ValueError(
                "fandom mode needs a world to narrate — name a folder under "
                "configs/fandoms/ (slopgen fandom <lang> <name>)"
            )
        raise ValueError(
            f"fandom '{ctx.params.fandom}' not found — expected a folder at "
            f"configs/fandoms/{ctx.params.fandom}/"
        )
    lore = ctx.lore
    if not lore.strip():
        raise ValueError(
            f"fandom '{fandom.name}' has no lore to narrate — put at least one "
            f"markdown document in {fandom.root}/"
        )
    if len(lore) < SMALL_LORE_CHARS:
        job.canon = ""  # the writer gets the documents themselves
        return
    if job.canon:
        return  # a resumed run keeps the world it started on (see the module docstring)

    fresh = recompile_if_stale(ctx.llm, fandom, lore, LANG_NAMES.get(ctx.params.lang, ctx.params.lang))
    if fresh is not fandom:
        write_fandom(fresh)
        ctx.store.fandoms[fresh.name] = fresh
    job.canon = fresh.canon
    ctx.progress("canon", 1, 1)
