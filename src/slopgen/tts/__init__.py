"""Voice engines: who says the lines, and where the word timings come from.

`base.ENGINES` is the catalogue (id, what it costs, whether it gives timings, whether
it clones); `base.build` instantiates one, importing only that engine's dependencies.
`align` supplies the timings for the engines that give none.
"""

from .base import (
    ENGINES,
    VOICE_PRESETS,
    EngineInfo,
    TTSEngine,
    TTSError,
    Voice,
    apply_rate,
    build,
    gives_timings,
    rate_factor,
    verify_take,
    voice_presets,
)

__all__ = [
    "ENGINES", "VOICE_PRESETS", "EngineInfo", "TTSEngine", "TTSError", "Voice",
    "apply_rate", "build", "gives_timings", "rate_factor", "verify_take", "voice_presets",
]
