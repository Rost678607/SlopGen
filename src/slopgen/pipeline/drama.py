"""AI-drama planning helpers shared by the drama script and footage stages.

The heart is :func:`plan_slots`: it turns the orchestration (an ordered list of
AI generators, each with a metric/amount) plus a target length into a concrete,
ordered list of *slots* — one per clip/shot — each pinned to the generator that
will make it and its nominal length. The script stage sizes one narration beat
per slot; the footage stage generates one clip per slot.

Timeline rules (decided with the user):
  * Length is authored in minutes ± a seconds tolerance; that budget is the
    authority. The tolerance is the scriptwriter's creative leeway (it may emit a
    few more/fewer beats) and is reconciled against the slots at generation time.
  * Orchestration only *routes* which generator makes each clip. Metrics mix
    freely (hybrid): ``percent`` = a share of the budget, ``seconds`` / ``clips``
    = an absolute chunk of real timeline, and the LAST stage always absorbs
    whatever budget remains.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.models import OrchestrationConfig, OrchestrationStage
from ..media.generate import is_video_model, model_clip_seconds

# rough speaking rate (words per second) used to size a beat's narration to its
# clip length; the per-scene atempo stretch later absorbs the residual error.
_WORDS_PER_SEC: dict[str, float] = {"en": 2.3, "ru": 2.0}
_DEFAULT_WPS = 2.2

MAX_SLOTS = 240  # hard safety cap on clips per video (runaway budgets/configs)


@dataclass
class Slot:
    """One planned clip: which generator makes it and how long it nominally is."""

    model: str
    key_mode: str
    key: str
    clip_seconds: float
    is_video: bool


def _stage_budget_s(stage: OrchestrationStage, total_s: float) -> float:
    """The seconds of timeline a stage claims, per its metric (before remainder
    fill / truncation)."""
    if stage.metric == "percent":
        return max(stage.amount, 0.0) / 100.0 * total_s
    if stage.metric == "seconds":
        return max(stage.amount, 0.0)
    # clips
    return max(stage.amount, 0.0) * model_clip_seconds(stage.model)


def plan_slots(orch: OrchestrationConfig | None, total_s: float) -> list[Slot]:
    """Expand the orchestration into an ordered per-clip slot list filling
    ``total_s`` seconds. Stages are walked in order; each non-last stage takes its
    metric's chunk (capped so it never overshoots the budget), and the last stage
    fills whatever remains. Always returns at least one slot."""
    stages = (orch.stages if orch and orch.stages else None) or [
        OrchestrationStage(model="wan2.1", metric="percent", amount=100.0)
    ]
    total_s = max(total_s, 1.0)

    slots: list[Slot] = []
    consumed = 0.0
    for i, st in enumerate(stages):
        is_last = i == len(stages) - 1
        remaining = max(total_s - consumed, 0.0)
        if is_last:
            budget = remaining
        else:
            budget = min(_stage_budget_s(st, total_s), remaining)
        cs = model_clip_seconds(st.model)
        n = round(budget / cs) if budget > 0 else 0
        # the last stage must contribute at least one clip if nothing has yet
        if is_last and not slots:
            n = max(n, 1)
        for _ in range(n):
            if len(slots) >= MAX_SLOTS:
                return slots
            slots.append(Slot(st.model, st.key_mode, st.key, cs, is_video_model(st.model)))
        consumed += n * cs
        if consumed >= total_s and not is_last:
            break  # absolute stages already filled the budget
    return slots or [
        Slot("wan2.1", "rotate", "", model_clip_seconds("wan2.1"), is_video_model("wan2.1"))
    ]


def word_budget(seconds: float, lang: str) -> int:
    """Roughly how many spoken words fit in `seconds` of narration for `lang`."""
    return max(3, round(seconds * _WORDS_PER_SEC.get(lang, _DEFAULT_WPS)))
