"""AppContext: resolved configs + shared clients handed to every stage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import (
    AccountConfig,
    AdConfig,
    CharacterConfig,
    ConfigStore,
    ContentTypeConfig,
    GlobalConfig,
    OrchestrationConfig,
    RunParams,
    VisualsConfig,
)
from ..llm import ChatLLM

# Stand-in for the "no content type" ("auto") choice: empty briefs/voices/
# fallbacks so nothing about a niche leaks into the prompts.
_AUTO_CONTENT = ContentTypeConfig(name="", idea_brief={}, script_brief={}, voices={})


@dataclass
class AppContext:
    store: ConfigStore
    params: RunParams
    llm: object = None
    used_clips: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.llm = ChatLLM(self.store.active_llm_profile())

    @property
    def g(self) -> GlobalConfig:
        return self.store.global_cfg

    @property
    def content(self) -> ContentTypeConfig:
        """The chosen content type, or a blank one when none was picked ("auto").
        The blank config carries empty briefs / voices / fallbacks, so the idea
        and script stages inject nothing about a niche and the LLM is free to
        pick any topic."""
        ct = self.store.content_types.get(self.params.content_type)
        return ct if ct else _AUTO_CONTENT

    @property
    def visuals(self) -> VisualsConfig:
        if self.params.manual_visuals:
            return self.params.manual_visuals
        return self.store.visuals.get(self.params.visuals) or VisualsConfig(name="classic")

    @property
    def ad(self) -> AdConfig | None:
        if self.params.manual_ad:
            return self.params.manual_ad
        return self.store.ads.get(self.params.ad) if self.params.ad else None

    @property
    def account(self) -> AccountConfig | None:
        return self.store.accounts.get(self.params.push) if self.params.push else None

    # -- AI-drama mode -----------------------------------------------------

    @property
    def is_drama(self) -> bool:
        return self.params.mode == "drama"

    @property
    def cast(self) -> list[CharacterConfig]:
        """The drama's cast for this run (ad-hoc from the TUI, or resolved from
        the library by the CLI). Empty = no fixed cast (the writer improvises)."""
        return list(self.params.manual_cast)

    @property
    def orchestration(self) -> OrchestrationConfig | None:
        """The AI-generator chain for the drama's video: an ad-hoc one from the
        TUI, else a named profile, else None (the planner falls back to a default)."""
        if self.params.manual_orchestration:
            return self.params.manual_orchestration
        return self.store.orchestrations.get(self.params.orchestration) if self.params.orchestration else None

    @property
    def llm_web_search(self) -> bool:
        """Whether the active LLM profile wants live web access for the script."""
        return getattr(self.store.active_llm_profile(), "web_search", False)

    @property
    def native_ad_on(self) -> bool:
        return bool(
            self.ad
            and self.ad.native
            and "native" in self.ad.modes
            and self.params.ad_mode in ("native", "both")
        )

    @property
    def overlay_ad_on(self) -> bool:
        return bool(
            self.ad
            and self.ad.overlay
            and "overlay" in self.ad.modes
            and self.params.ad_mode in ("overlay", "both")
        )

    # -- topic history for dedup -------------------------------------------

    @property
    def history_file(self) -> Path:
        return self.g.paths.state / "history.json"

    def load_history(self) -> list[dict]:
        if self.history_file.exists():
            return json.loads(self.history_file.read_text())
        return []

    def append_history(self, entry: dict) -> None:
        hist = self.load_history()
        hist.append(entry)
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(hist, ensure_ascii=False, indent=1))
