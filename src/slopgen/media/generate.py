"""AI generators used as footage providers.

`pollinations` — free, keyless text-to-image (FLUX/SD via pollinations.ai). Slots
into the photo chain (`find_image`) exactly like Pexels/Pixabay: give it a prompt,
get a portrait JPG back.

`wan` — free text-to-video via Hugging Face Spaces (Wan 2.1 and lighter reserves
like LTX-Video / AnimateDiff). Slots into the clip chain (`find_clip`). Spaces are
tried in order; the first that yields a video wins, and any failure (queue, cold
Space, changed API) silently falls through to the next Space and then to the next
provider — free video hosting is flaky by nature, so this provider degrades rather
than aborting a run.

Both cache generated assets on disk under state/cache/ keyed by the prompt, so the
same prompt across scenes/runs isn't regenerated (video generation is slow: expect
minutes per clip).
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import httpx

from .stock import FootageError, _download

# Reserve chain for the `wan` provider: portrait text-to-video Spaces, best quality
# first, lighter/faster fallbacks after. Space ids drift over time (HF removes/renames
# them constantly) — override via [footage] video_gen_spaces in configs/slopgen.toml
# when one goes offline. Verified live 2026-07; the old Wan-AI/Wan2.1-T2V-14B and
# Lightricks/LTX-Video Spaces were removed. The base Wan-AI/Wan2.1 Space is alive but
# uses an async submit+poll API that _run_space can't drive, so it's intentionally out.
DEFAULT_VIDEO_SPACES = [
    "DeepRat/LTX-Video-ZeroGPU-Optimized",  # LTX-Video DiT text-to-video, ZeroGPU
    "ByteDance/AnimateDiff-Lightning",       # fast anime-ish reserve
]

# Friendly generator names surfaced in the TUI picker → concrete settings.
# `ai_video` (find_clip via `wan`): each name pins the HF Space reserve chain.
VIDEO_MODELS: dict[str, list[str]] = {
    "auto": list(DEFAULT_VIDEO_SPACES),  # try all, best quality first
    "wan2.1": list(DEFAULT_VIDEO_SPACES),  # native Wan Spaces are gone/async-only → live chain
    "ltx-video": ["DeepRat/LTX-Video-ZeroGPU-Optimized"],
    "animatediff": ["ByteDance/AnimateDiff-Lightning"],
}
# `ai_photo` (find_image via `pollinations`): name maps to a pollinations model.
PHOTO_MODELS: dict[str, str] = {
    "flux": "flux",
    "turbo": "turbo",
}

# Expected output length, in seconds, of one clip/shot from each generator. Drives
# the AI-drama timeline: how many words of narration a scene gets (words ≈ seconds
# × speaking rate) and the base length the voiceover is time-stretched to. Video
# Spaces emit a roughly fixed length we can't set precisely; images have none, so
# their value is just the on-screen (Ken-Burns) duration we give the still. These
# are nominal — the real generated length (video) is measured and used for the
# stretch; the nominal only sizes the script up front.
MODEL_CLIP_SECONDS: dict[str, float] = {
    "auto": 5.0,
    "wan2.1": 5.0,
    "ltx-video": 4.0,
    "animatediff": 3.0,
    "flux": 5.0,
    "turbo": 5.0,
}
DEFAULT_CLIP_SECONDS = 5.0


def model_clip_seconds(model: str) -> float:
    """Nominal clip length for a generator name (see MODEL_CLIP_SECONDS)."""
    return MODEL_CLIP_SECONDS.get(model, DEFAULT_CLIP_SECONDS)


def is_video_model(model: str) -> bool:
    """True when `model` is a text-to-video generator (find_clip via `wan`), False
    for a text-to-image one (find_image via `pollinations`)."""
    return model not in PHOTO_MODELS


def key_var_for_model(model: str) -> str:
    """The .env variable that holds the API key(s) for a generator model."""
    return "POLLINATIONS_TOKEN" if model in PHOTO_MODELS else "HF_TOKEN"


def env_keys(var: str) -> list[str]:
    """The individual API keys in a comma/newline-separated .env variable (for
    multi-key rotation). A plain single key comes back as a one-item list."""
    return [k.strip() for k in re.split(r"[,\n]", os.environ.get(var, "")) if k.strip()]


@dataclass
class GenParams:
    """Everything the AI providers need, built once per run from the config."""

    width: int = 1080
    height: int = 1920
    pollinations_model: str = "flux"
    video_spaces: list[str] = field(default_factory=lambda: list(DEFAULT_VIDEO_SPACES))
    style_suffix: str = ""  # appended to every generated prompt (e.g. "anime style")
    hf_token: str | None = None  # optional; cuts HF Space queue times (wan)
    pollinations_token: str | None = None  # optional; higher rate limits / tier


def _prompt(query: str, gen: GenParams) -> str:
    q = query.strip()
    return f"{q}, {gen.style_suffix}".strip(", ") if gen.style_suffix else q


# --- images: pollinations.ai ------------------------------------------------


def pollinations_image(
    query: str, cache_dir: Path, exclude: set[str], gen: GenParams
) -> Path | None:
    """Generate one portrait image for `query`. Free, no API key."""
    prompt = _prompt(query, gen)
    if not prompt:
        return None
    # random seed => a fresh image each call (variety across scenes) and a unique
    # cache file per URL; the on-disk cache still spares repeats of the same URL.
    seed = random.randint(1, 2**31 - 1)
    url = (
        f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        f"?width={gen.width}&height={gen.height}&model={gen.pollinations_model}"
        f"&nologo=true&seed={seed}"
    )
    if gen.pollinations_token:  # authenticated tier: higher limits, no watermark
        url += f"&token={quote(gen.pollinations_token)}"
    if url in exclude:
        return None
    exclude.add(url)
    dest = cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".jpg")
    # pollinations occasionally 5xx under load; a couple of retries usually clears it
    for attempt in range(3):
        try:
            return _download(url, dest)
        except httpx.HTTPError:
            if attempt == 2:
                return None
    return None


# --- video: Hugging Face Spaces (Wan and reserves) --------------------------


def _extract_video(result) -> str | None:
    """Pull a local file path out of a gradio predict() result of unknown shape."""
    if result is None:
        return None
    if isinstance(result, str):
        return result if result.endswith((".mp4", ".webm", ".mov", ".gif")) else None
    if isinstance(result, dict):
        for k in ("video", "path", "name", "url"):
            p = _extract_video(result.get(k))
            if p:
                return p
        return None
    if isinstance(result, (list, tuple)):
        for item in result:
            p = _extract_video(item)
            if p:
                return p
    return None


# Known text-to-video endpoints, tried in order. Each is (api_name, extra_kwargs):
# the prompt is passed by keyword, so a Space whose first input isn't named 'prompt'
# just errors and we fall through. `mode` pins LTX Spaces to text-to-video (their
# default is image-to-video, which fails without an input image). `None` is a last
# resort: a bare positional predict for Spaces whose text input is named differently.
_VIDEO_ENDPOINTS: tuple[tuple[str | None, dict], ...] = (
    ("/text_to_video", {"mode": "text-to-video"}),  # DeepRat LTX-Video (+ LTX forks)
    ("/generate_video", {}),
    ("/generate_image", {}),  # ByteDance AnimateDiff-Lightning
    ("/generate", {}),
    ("/run", {}),
    ("/predict", {}),
    (None, {}),
)


def _run_space(space: str, prompt: str, token: str | None) -> str | None:
    """Best-effort call into a text-to-video Space. Space APIs vary wildly, so try
    the known endpoint shapes (see _VIDEO_ENDPOINTS) and take whatever video path
    comes back. Raises on hard failures (missing dep, dead Space)."""
    from gradio_client import Client  # lazy: only when the wan provider is used

    client = Client(space, token=token, verbose=False)
    last: Exception | None = None
    for api, extra in _VIDEO_ENDPOINTS:
        try:
            if api is None:
                result = client.predict(prompt)  # positional: odd/renamed text input
            else:
                result = client.predict(api_name=api, prompt=prompt, **extra)
        except Exception as e:  # wrong api_name / arg name / arg count — try the next shape
            last = e
            continue
        path = _extract_video(result)
        if path:
            return path
    if last is not None:
        raise last
    return None


def wan_video(
    query: str, cache_dir: Path, exclude: set[str], gen: GenParams
) -> Path | None:
    """Generate one portrait clip for `query` via the HF Space reserve chain.
    Returns None (not an error) if every Space is unavailable, so find_clip can
    fall through to the next provider."""
    prompt = _prompt(query, gen)
    if not prompt:
        return None
    try:
        import gradio_client  # noqa: F401
    except ImportError as e:
        raise FootageError(
            "the 'wan' provider needs gradio_client — `pip install gradio_client` "
            "(or remove 'wan' from [footage] providers)"
        ) from e

    dest = cache_dir / (hashlib.sha1(("wan:" + prompt).encode()).hexdigest() + ".mp4")
    if dest.exists():
        return dest

    token = gen.hf_token or os.environ.get("HF_TOKEN") or None
    for space in gen.video_spaces:
        try:
            local = _run_space(space, prompt, token)
        except Exception:
            continue  # dead/changed Space — move to the next reserve
        if local and Path(local).exists():
            shutil.copy(local, dest)
            exclude.add(str(dest))
            return dest
    return None
