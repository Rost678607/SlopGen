"""AppContext: resolved configs + shared clients handed to every stage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import (
    AccountConfig,
    AdConfig,
    CharacterConfig,
    ConfigStore,
    ContentTypeConfig,
    FandomConfig,
    GlobalConfig,
    OrchestrationConfig,
    RunParams,
    VisualsConfig,
)
from ..config.loader import lore_sha, read_lore
from ..llm import ChatLLM
from ..llm.style import compile_style

# Stand-in for the "no content type" ("auto") choice: empty briefs/voices/
# fallbacks so nothing about a niche leaks into the prompts.
_AUTO_CONTENT = ContentTypeConfig(name="", idea_brief={}, script_brief={}, voices={})


@dataclass
class AppContext:
    store: ConfigStore
    params: RunParams
    llm: object = None
    used_clips: set[str] = field(default_factory=set)
    # optional (unit, done, total) sink so a stage can report progress WITHIN itself:
    # the orchestrator's event stream only fires between stages, which leaves the
    # long ones (voicing 40 lines, generating 40 clips) looking frozen.
    on_progress: Callable[[str, int, int], None] | None = None
    # compiled look, filled on first use (see `style_suffix`); None = not yet compiled,
    # "" = nothing to compile. Never set by the caller.
    _style: str | None = None

    def __post_init__(self):
        self.llm = ChatLLM(self.store.active_llm_profile())

    def progress(self, unit: str, done: int, total: int) -> None:
        """Report `done of total` for a stage's inner loop. Never raises: a broken
        or missing reporter must not take the pipeline down."""
        if self.on_progress is None:
            return
        try:
            self.on_progress(unit, done, total)
        except Exception:
            pass

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
    def medium(self) -> str:
        """What the picture is made of — "photo", "video", or "" when the run mixes
        the two. Only fandom asks the operator outright (`params.medium`); the other
        modes answer it by what they were pointed at, an info clip through its visuals
        profile and a beat mode through its generator chain."""
        if self.params.medium:
            return self.params.medium
        if not self.is_beats:
            src = self.visuals.background.source
            return "photo" if src.endswith("_photo") else "video" if src.endswith("_video") else ""
        orch = self.orchestration
        if not orch or not orch.stages:
            return ""
        from ..media.generate import is_video_model

        kinds = {"video" if is_video_model(s.model) else "photo" for s in orch.stages}
        return kinds.pop() if len(kinds) == 1 else ""

    @property
    def style_suffix(self) -> str:
        """Everything appended to EVERY generated prompt of this run: the operator's
        look, compiled into English tags, plus the standing suffix from
        `[footage] gen_style_suffix`.

        It is compiled here rather than in a stage because it belongs to the run and
        not to any one of them — all three modes want it, the info chain has nowhere
        to put a drama-shaped stage, and both the automatic generators and the prompts
        handed to an operator making shots by hand read the same string. The compile
        is lazy and memoised (and cached on disk by `llm/style`), so a run that
        generates nothing never pays for it and a resume never pays twice."""
        if self._style is None:
            self._style = compile_style(
                self.llm,
                self.params.visual_style,
                cache_dir=self.g.paths.state / "cache" / "styles",
                medium=self.medium,
            )
        return ", ".join(
            s.strip() for s in (self._style, self.g.footage.gen_style_suffix) if s.strip()
        )

    @property
    def ad(self) -> AdConfig | None:
        if self.params.manual_ad:
            return self.params.manual_ad
        return self.store.ads.get(self.params.ad) if self.params.ad else None

    @property
    def account(self) -> AccountConfig | None:
        return self.store.accounts.get(self.params.push) if self.params.push else None

    # -- the beat-based modes (drama, fandom) ------------------------------

    @property
    def is_drama(self) -> bool:
        return self.params.mode == "drama"

    @property
    def is_fandom(self) -> bool:
        return self.params.mode == "fandom"

    @property
    def is_beats(self) -> bool:
        """Whether the run is built out of BEATS — a story cut into one-clip beats,
        voiced per scene, cut into episodes and shot by AI generators. Both drama and
        fandom are; info is not. Nearly everything that used to ask `is_drama` was
        really asking this, since the two modes share the whole tail of the pipeline
        and differ only in who writes the script and what it is written about."""
        return self.params.mode in ("drama", "fandom")

    @property
    def fandom(self) -> FandomConfig | None:
        """The world being narrated, or None outside fandom mode."""
        if not self.params.fandom:
            return None
        return self.store.fandoms.get(self.params.fandom)

    @property
    def lore(self) -> str:
        """The fandom's lore documents as one text (empty outside fandom mode)."""
        f = self.fandom
        return read_lore(f) if f else ""

    @property
    def lore_sha(self) -> str:
        """Checksum of the lore as it is on disk right now — compared against the
        fandom's `docs_sha` to decide whether its canon sheet is still current."""
        lore = self.lore
        return lore_sha(lore) if lore else ""

    @property
    def cast(self) -> list[CharacterConfig]:
        """Who may appear on screen.

        In drama this is whatever the operator assembled for the run: members are
        ad-hoc unless saved, pulled from the shared library, toggled in and out per
        video. A fandom's people are not like that — they belong to the world, are in
        it or are not, and nobody turns up for one video and vanishes after it. So
        fandom takes the world's characters and nothing else: there is no run cast to
        merge, no library to borrow from, and the TUI and CLI offer neither.
        Empty = no fixed cast (the writer improvises)."""
        f = self.fandom
        if f:
            return list(f.cast)
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
