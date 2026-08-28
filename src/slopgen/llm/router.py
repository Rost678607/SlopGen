"""Which model answers which call.

Every LLM call in slopgen already announces what it is — `complete_json("fandom_script",
…)`, `complete_json("char_compile", …)` — because the label was needed for error
messages. That label turns out to be the only thing a router needs, so routing costs no
new plumbing at all: this class stands where the single :class:`ChatLLM` used to
(`AppContext.llm`), keeps one client per profile it is actually asked for, and picks by
kind.

The reason to want it is that the calls are not alike. Writing a script off a whole
world, planning it into stretches, or answering an archivist's question about the lore
is what an expensive model is for. Compiling one character's appearance into image tags,
naming a finished video, turning a shot description into a stock-footage query or
rewriting a line to swear more is errand work — the difference between models shows up
in none of it, and on a fandom run the errands outnumber the writing.

Routing is opt-in and fails soft. `[llm.stage_profiles]` maps kind → profile name; a
kind that is not listed, or one naming a profile that does not exist, goes to the active
profile exactly as before. A profile that cannot be built at all (its key is missing
from `.env`) falls back to the active one too rather than taking the run down over an
errand — a wrongly-configured cheap model must not cost the operator a script.

The ledger is shared by every client here (see `llm/usage`), so a run split across two
providers still adds up to one bill, broken down by stage and by kind.
"""

from __future__ import annotations

import logging

from ..config.models import LLMProfile
from .client import ChatLLM
from .usage import UsageLedger

log = logging.getLogger(__name__)


class LLMRouter:
    """The run's LLM, as far as every stage is concerned.

    Presents exactly the surface `ChatLLM` does — `complete_json`, `complete_text`,
    `describe_image`, `model` — so nothing downstream knows it exists."""

    def __init__(self, store, ledger: UsageLedger | None = None):
        self.store = store
        self.usage = ledger if ledger is not None else UsageLedger()
        self.default_profile: LLMProfile = store.active_llm_profile()
        self.routes: dict[str, str] = dict(
            getattr(store.global_cfg.llm, "stage_profiles", {}) or {}
        )
        self._clients: dict[str, ChatLLM] = {}
        # built eagerly: a missing API key is the operator's first mistake and belongs
        # at the start of a run, not four stages into it
        self._default = self._build(self.default_profile)

    # -- the clients -------------------------------------------------------

    def _build(self, profile: LLMProfile) -> ChatLLM:
        return ChatLLM(profile, ledger=self.usage)

    def client_for(self, kind: str) -> ChatLLM:
        """The client that answers calls of this kind — the routed profile if it is
        configured, exists and can be built, else the run's active one."""
        name = self.routes.get(kind, "")
        if not name or name == self.default_profile.name:
            return self._default
        if name in self._clients:
            return self._clients[name]
        profile = self.store.llm_profiles.get(name)
        if profile is None:
            log.warning("[llm.stage_profiles] %s = '%s': no such profile", kind, name)
            self._clients[name] = self._default
            return self._default
        try:
            self._clients[name] = self._build(profile)
        except Exception as e:  # noqa: BLE001 — an errand's misconfiguration is not a run
            log.warning("profile '%s' unusable (%s); '%s' stays on %s",
                        name, e, kind, self.default_profile.name)
            self._clients[name] = self._default
        return self._clients[name]

    # -- the surface every stage already calls -----------------------------

    @property
    def model(self) -> str:
        return self._default.model

    def complete_json(self, kind: str, system: str, user: str, web_search: bool = False,
                      tools: dict | None = None, attempt: int = 0) -> dict:
        return self.client_for(kind).complete_json(
            kind, system, user, web_search=web_search, tools=tools, attempt=attempt
        )

    def complete_text(self, kind: str, system: str, user: str) -> str:
        return self.client_for(kind).complete_text(kind, system, user)

    def describe_image(self, prompt: str, image: bytes, mime: str = "image/jpeg") -> str:
        # the one call with no kind of its own: it is always a reference photo being
        # read, and a text-only model cannot serve it at all
        return self.client_for("vision").describe_image(prompt, image, mime)
