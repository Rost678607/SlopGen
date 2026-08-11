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
  * Clip length is authored too (run level, per-stage override, or the generator's
    nominal — see :func:`stage_clip_seconds`) and is FIXED: it decides how many
    clips the budget is cut into and how much narration each one carries. The
    writer sizes its beats to it and never changes it.
  * Orchestration only *routes* which generator makes each clip. Metrics mix
    freely (hybrid): ``percent`` = a share of the budget, ``seconds`` / ``clips``
    = an absolute chunk of real timeline, and the LAST stage always absorbs
    whatever budget remains.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.models import OrchestrationConfig, OrchestrationStage
from ..media.generate import is_video_model, model_clip_seconds

# rough speaking rate (words per second) at the voice's NATURAL speed, used to size a
# beat's narration to its clip length; the per-scene atempo stretch later absorbs the
# residual error.
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


def stage_clip_seconds(stage: OrchestrationStage, average_s: float = 0.0) -> float:
    """How long one clip from this stage nominally runs. The stage's own override
    wins, then the run-level average the operator set, then the generator's nominal
    length. Clip length is a property of where the clip comes from: a hand-made
    Kling/Veo shot can run 10-15s while an HF Space emits ~5s."""
    return max(stage.clip_seconds or average_s or model_clip_seconds(stage.model), 0.5)


def _stage_budget_s(stage: OrchestrationStage, total_s: float, clip_s: float) -> float:
    """The seconds of timeline a stage claims, per its metric (before remainder
    fill / truncation)."""
    if stage.metric == "percent":
        return max(stage.amount, 0.0) / 100.0 * total_s
    if stage.metric == "seconds":
        return max(stage.amount, 0.0)
    # clips
    return max(stage.amount, 0.0) * clip_s


def plan_slots(
    orch: OrchestrationConfig | None, total_s: float, average_clip_s: float = 0.0
) -> list[Slot]:
    """Expand the orchestration into an ordered per-clip slot list filling
    ``total_s`` seconds. Stages are walked in order; each non-last stage takes its
    metric's chunk (capped so it never overshoots the budget), and the last stage
    fills whatever remains. ``average_clip_s`` (0 = per-generator nominal) sets how
    long one clip runs, and so how many of them the budget is cut into. Always
    returns at least one slot."""
    stages = (orch.stages if orch and orch.stages else None) or [
        OrchestrationStage(model="wan2.1", metric="percent", amount=100.0)
    ]
    total_s = max(total_s, 1.0)

    slots: list[Slot] = []
    consumed = 0.0
    for i, st in enumerate(stages):
        is_last = i == len(stages) - 1
        remaining = max(total_s - consumed, 0.0)
        cs = stage_clip_seconds(st, average_clip_s)
        if is_last:
            budget = remaining
        else:
            budget = min(_stage_budget_s(st, total_s, cs), remaining)
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


def speech_scale(rate: int) -> float:
    """edge-tts' ``rate`` percentage as a factor on how fast the voice speaks.
    ``+25%`` says a quarter more words in the same second; ``-50%`` half as many."""
    return max(1.0 + rate / 100.0, 0.25)


def word_budget(seconds: float, lang: str, rate: int = 0) -> int:
    """Roughly how many spoken words fit in `seconds` of narration for `lang`, at the
    run's speech `rate`.

    The rate is what the operator set for the voiceover, and it belongs here because it
    changes how much STORY a clip of fixed length can hold: a beat voiced at +30% takes
    a third more words to fill the same shot. Sizing the writer's beats without it is
    what makes a fast run come out with a silent tail on every clip (the voice ends
    early) and a slow run come out clipped."""
    return max(3, round(seconds * _WORDS_PER_SEC.get(lang, _DEFAULT_WPS) * speech_scale(rate)))


# How many beats one LLM call handles. A whole feature-length script asked for in a
# single response is where detail goes to die: the model has one budget for the whole
# plot and spends it front-loaded, so the opening beats track the premise sentence by
# sentence and everything after is summary. Splitting the work gives each call a small,
# stated slice of the story and enough room to actually spend on it.
BEATS_PER_WINDOW = 14


def plan_windows(total: int, size: int = BEATS_PER_WINDOW) -> list[tuple[int, int]]:
    """Split `total` beats into consecutive ``[start, end)`` windows of at most
    `size`. The windows are balanced rather than greedy — 30 beats become 15+15,
    not 14+14+2 — because a stub last window is a whole LLM call asked to write the
    ending in two beats, which is exactly where a rushed payoff comes from."""
    if total <= 0:
        return []
    count = -(-total // max(size, 1))  # ceil
    base, extra = divmod(total, count)
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(count):
        end = start + base + (1 if i < extra else 0)
        out.append((start, end))
        start = end
    return out
