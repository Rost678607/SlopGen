"""Discovery and loading of TOML configs from the configs/ tree."""

from __future__ import annotations

import tomllib
from pathlib import Path

from .models import (
    AccountConfig,
    AdConfig,
    CharacterConfig,
    ContentTypeConfig,
    GlobalConfig,
    LLMProfile,
    OrchestrationConfig,
    PresetConfig,
    RunParams,
    VisualsConfig,
)

CONFIGS_DIR = Path("configs")


class ConfigError(Exception):
    pass


def _read_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}")


def _load_dir(subdir: str, model):
    out = {}
    d = CONFIGS_DIR / subdir
    if d.is_dir():
        for p in sorted(d.glob("*.toml")):
            data = _read_toml(p)
            data.setdefault("name", p.stem)
            out[data["name"]] = model.model_validate(data)
    return out


class ConfigStore:
    """All configs, loaded once. Reload by constructing a new instance."""

    def __init__(self, root: Path | None = None):
        global CONFIGS_DIR
        if root:
            CONFIGS_DIR = root
        gpath = CONFIGS_DIR / "slopgen.toml"
        self.global_cfg = (
            GlobalConfig.model_validate(_read_toml(gpath)) if gpath.exists() else GlobalConfig()
        )
        self.content_types: dict[str, ContentTypeConfig] = _load_dir("content", ContentTypeConfig)
        self.ads: dict[str, AdConfig] = _load_dir("ads", AdConfig)
        self.accounts: dict[str, AccountConfig] = _load_dir("accounts", AccountConfig)
        self.presets: dict[str, PresetConfig] = _load_dir("presets", PresetConfig)
        self.visuals: dict[str, VisualsConfig] = _load_dir("visuals", VisualsConfig)
        self.llm_profiles: dict[str, LLMProfile] = _load_dir("llm", LLMProfile)
        self.characters: dict[str, CharacterConfig] = _load_dir("characters", CharacterConfig)
        self.orchestrations: dict[str, OrchestrationConfig] = _load_dir("orchestration", OrchestrationConfig)

    def active_llm_profile(self) -> LLMProfile:
        """Profile named in [llm].profile, else first profile, else a legacy
        profile synthesized from the inline [llm] fields."""
        llm = self.global_cfg.llm
        if llm.profile and llm.profile in self.llm_profiles:
            return self.llm_profiles[llm.profile]
        if self.llm_profiles:
            return next(iter(self.llm_profiles.values()))
        return LLMProfile(
            name="legacy",
            provider=llm.provider,
            base_url=llm.base_url,
            model=llm.model,
            key_env=llm.key_env,
            temperature=llm.temperature,
        )

    def languages(self) -> list[str]:
        langs: set[str] = set()
        for ct in self.content_types.values():
            langs.update(ct.voices.keys())
        return sorted(langs)

    # -- parameter resolution: CLI > preset > account defaults > global ----

    def resolve(
        self,
        lang: str | None = None,
        content_type: str | None = None,
        ad: str | None = None,
        ad_mode: str | None = None,
        visuals: str | None = None,
        duration_s: float | None = None,
        profanity: int | None = None,
        push: str | None = None,
        count: int | None = None,
        preset: str | None = None,
        **extra,
    ) -> RunParams:
        p = self.presets.get(preset) if preset else None
        if preset and not p:
            raise ConfigError(f"preset '{preset}' not found")

        push_val = push if push is not None else (p.push if p else "")
        acc = self.accounts.get(push_val) if push_val else None
        if push_val and not acc:
            raise ConfigError(f"account '{push_val}' not found")
        ad_def = acc.defaults if acc else None

        def pick(cli, preset_v, acc_v, default):
            for v in (cli, preset_v, acc_v):
                if v not in (None, ""):
                    return v
            return default

        g = self.global_cfg.defaults
        params = RunParams(
            lang=pick(lang, p.lang if p else None, ad_def.lang if ad_def else None, ""),
            content_type=pick(
                content_type,
                p.content_type if p else None,
                ad_def.content_type if ad_def else None,
                "",
            ),
            ad=pick(ad, p.ad if p else None, ad_def.ad if ad_def else None, ""),
            ad_mode=pick(ad_mode, p.ad_mode if p else None, ad_def.ad_mode if ad_def else None, g.ad_mode),
            visuals=pick(
                visuals, p.visuals if p else None, ad_def.visuals if ad_def else None, "classic"
            ),
            duration_s=pick(
                duration_s,
                p.duration_s if p else None,
                ad_def.duration_s if ad_def else None,
                self.global_cfg.video.target_duration_s,
            ),
            profanity=pick(
                profanity,
                p.profanity if p else None,
                ad_def.profanity if ad_def else None,
                g.profanity,
            ),
            push=push_val,
            count=pick(count, p.count if p else None, None, g.count),
            **extra,
        )

        if not params.lang or not params.content_type:
            raise ConfigError(
                "language and content type are required "
                "(pass as arguments, or via --preset / account defaults)"
            )
        if params.content_type not in self.content_types:
            raise ConfigError(
                f"unknown content type '{params.content_type}' "
                f"(available: {', '.join(self.content_types)})"
            )
        ct = self.content_types[params.content_type]
        if params.lang not in ct.voices:
            raise ConfigError(
                f"content type '{params.content_type}' has no voice for language "
                f"'{params.lang}' (available: {', '.join(ct.voices)})"
            )
        if params.ad and params.ad not in self.ads:
            raise ConfigError(f"ad contract '{params.ad}' not found (available: {', '.join(self.ads)})")
        if (
            not params.manual_visuals
            and params.visuals
            and params.visuals not in self.visuals
        ):
            raise ConfigError(
                f"visuals profile '{params.visuals}' not found (available: {', '.join(self.visuals)})"
            )
        return params
