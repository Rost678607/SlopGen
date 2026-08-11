"""Drama stage 4: settle where one episode ends and the next begins.

The writer already labelled every scene with a part number, so on an untouched run
this stage only turns those labels into the episode list the rest of the pipeline
works through (:func:`..parts.sync`). What it is really for is the breakpoint it
carries: it is the last moment at which re-cutting the drama is free.

Why here, and not later. By this point the voiceover exists, so every scene knows how
many seconds it actually runs — the boundaries can be judged on real minutes rather
than on the writer's estimate. And nothing expensive has happened yet: the next stage
is the one that generates the shots, or, on the user-assisted path, hands the operator
the shot list for episode 1 to go and make by hand. Move a boundary after that and the
work has already been done against the old one.
"""

from __future__ import annotations

from .. import parts
from ..context import AppContext
from ..job import VideoJob


def run(job: VideoJob, ctx: AppContext) -> None:
    # a re-cut at this breakpoint (or at the script one) may have left gaps in the
    # labels; close them so part numbers keep meaning "the Nth episode"
    parts.renumber(job.scenes)
    parts.sync(job)
    ctx.progress("cut", len(job.parts), len(job.parts))
