"""The catalogue of downloadable neural models — what exists, not what is installed.

Weights are NOT in the repository and never will be: the local Qwen voice alone is
2.3 GiB, which is two orders of magnitude more than everything else here put
together. So the repo ships this declaration — an id, where the files live, how big
they are, what licence they carry and which pip packages they need — and
:mod:`.store` turns a declaration into a folder on disk when the operator asks for it.

Sizes are exact, taken from the hosts' own file listings. They are what the manager
can promise BEFORE a download starts («2.3 GiB, Apache-2.0 — go on?»), and what its
progress bar is a fraction of; the transfer itself trusts the server's
`Content-Length`, so a model that gets re-uploaded a byte larger still installs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

HF = "https://huggingface.co/{repo}/resolve/main/{path}"


@dataclass(frozen=True)
class ModelFile:
    path: str  # where it goes, relative to the model's own folder
    url: str
    size: int = 0  # bytes; 0 = unknown, progress falls back to the server's answer


@dataclass(frozen=True)
class ModelSpec:
    """One installable thing. `packages` are pip requirements the model is useless
    without — torch for a neural voice, the vosk bindings for a recognizer — and the
    manager installs them as a separate, explicit step (see
    :meth:`.store.ModelStore.install_packages`), because a 900 MiB wheel is a bigger
    decision than a 46 MiB model and must not ride in on one silently.

    `unpack` marks an archive that becomes the model folder itself: vosk ships one zip
    with a versioned directory inside it, which is stripped so the folder on disk is
    named by our id and not by the release we happened to fetch."""

    id: str
    label: str
    description: str
    license: str
    files: tuple[ModelFile, ...]
    packages: tuple[str, ...] = ()
    # Some requirements are served from somewhere other than PyPI. The one that
    # matters here is torch: on Linux the PyPI wheel is the CUDA build, several
    # gigabytes of nvidia-* libraries that a machine with no discrete GPU downloads,
    # installs and never loads. `cpu_index` names the wheel index to use instead when
    # no CUDA device is visible — the store decides which (see store._pip_index).
    cpu_index: str = ""
    unpack: str = ""  # "" | "zip"
    strip_root: bool = True  # zip only: drop the archive's single top folder
    # what refuses to work until this is installed, for the "why do I need it" line
    used_by: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def _qwen_files() -> tuple[ModelFile, ...]:
    repo = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    sizes = {
        "config.json": 4494,
        "generation_config.json": 245,
        "preprocessor_config.json": 127,
        "tokenizer_config.json": 7344,
        "vocab.json": 2776833,
        "merges.txt": 1671839,
        "speech_tokenizer/config.json": 2336,
        "speech_tokenizer/configuration.json": 76,
        "speech_tokenizer/preprocessor_config.json": 234,
        "speech_tokenizer/model.safetensors": 682293092,
        "model.safetensors": 1829344272,
    }
    return tuple(
        ModelFile(path=p, url=HF.format(repo=repo, path=p), size=s) for p, s in sizes.items()
    )


def _vosk(model_id: str, release: str, size: int, lang: str) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        label=f"Vosk {lang} (small)",
        description=(
            "Speech recognizer used ONLY for word timings, never for the text: the "
            "words are already known, the recognizer just says when each one is said."
        ),
        license="Apache-2.0",
        files=(ModelFile(path=f"{release}.zip",
                         url=f"https://alphacephei.com/vosk/models/{release}.zip",
                         size=size),),
        packages=("vosk>=0.3.45",),
        unpack="zip",
        used_by="tts.align — required by every engine that returns no word timings",
        tags=("align", lang.lower()),
    )


CATALOG: dict[str, ModelSpec] = {
    "qwen3-tts-0.6b": ModelSpec(
        id="qwen3-tts-0.6b",
        label="Qwen3-TTS 12Hz 0.6B Base",
        description=(
            "Local voice cloning: hand it 10-20 s of a voice plus what is said in it "
            "and it speaks your lines in that voice. CPU-only here — measured RTF 5.05 "
            "on 8 threads in bfloat16, i.e. a minute of speech costs about five."
        ),
        license="Apache-2.0",
        files=_qwen_files(),
        packages=("torch>=2.4", "qwen-tts", "soundfile>=0.12"),
        # This engine is CPU-only by design and by measurement, and on this machine the
        # CUDA wheels came to ~3 GiB of libraries nothing would ever call.
        cpu_index="https://download.pytorch.org/whl/cpu",
        used_by="tts engine 'qwen-local'",
        tags=("tts", "clone"),
    ),
    "vosk-ru-small": _vosk("vosk-ru-small", "vosk-model-small-ru-0.22", 46236750, "Russian"),
    "vosk-en-small": _vosk("vosk-en-small", "vosk-model-small-en-us-0.15", 41205931, "English"),
    "rnnoise-sh": ModelSpec(
        id="rnnoise-sh",
        label="RNNoise «somnolent hogwash»",
        description=(
            "Denoiser for voice REFERENCES, run through ffmpeg's arnndn. Measured "
            "+11 dB of noise floor on a phone recording, and unlike spectral "
            "denoising it leaves no musical-noise artifacts for the cloner to imitate."
        ),
        license="BSD-3-Clause",
        files=(ModelFile(
            path="sh.rnnn",
            url=("https://raw.githubusercontent.com/GregorR/rnnoise-models/master/"
                 "somnolent-hogwash-2018-09-01/sh.rnnn"),
            size=297646,
        ),),
        used_by="`slopgen voices add --clean`",
        tags=("audio",),
    ),
}


def get(model_id: str) -> ModelSpec:
    try:
        return CATALOG[model_id]
    except KeyError:
        raise KeyError(
            f"unknown model '{model_id}'. Known: {', '.join(sorted(CATALOG))}"
        ) from None


def human_size(n: int) -> str:
    if n <= 0:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GiB"
