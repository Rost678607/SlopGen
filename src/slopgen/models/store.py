"""Installing, verifying and removing the models declared in :mod:`.registry`.

Two things here are deliberately not delegated to a library.

**The downloader.** `huggingface_hub` hung twice on this project's connection — 0.6 KB/s
over a socket that was never idle long enough to trip any read timeout, with no
progress and no error, forever. A read timeout cannot catch that, because bytes ARE
arriving; only a *rate* check can. So :func:`_stream_to` watches throughput over a
sliding window and hangs up on a transfer that has gone slower than
:data:`MIN_BYTES_PER_S`, then resumes from where it stopped via a `Range` request.
An interrupted 2.3 GiB install therefore costs the operator the bytes already on disk
and nothing more — which is also why the partial file is kept on failure rather than
cleaned up.

**The integrity check.** There are no published checksums to compare against for every
file here, and a length check alone passes on a truncated-then-padded file. But a
`.safetensors` file carries its own map: a JSON header naming every tensor's byte
range. If the last range ends exactly at the end of the file, the payload is the
length the producer meant it to be — cheap, and it catches the failure that actually
happens (a resume that stitched the ranges together wrong).
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path

import httpx

from .registry import CATALOG, ModelFile, ModelSpec, get, human_size

# a transfer slower than this for a whole window is treated as dead and restarted;
# 3 KB/s is far below any real connection and far above the 0.6 KB/s hang we hit.
MIN_BYTES_PER_S = 3000
STALL_WINDOW_S = 45.0
MAX_ATTEMPTS = 40  # each one resumes, so this is "how many drops we tolerate"
CHUNK = 1 << 16

MARKER = ".slopgen-installed.json"

# import name for a pip requirement, where they differ
_IMPORT_NAME = {"qwen-tts": "qwen_tts", "torch": "torch", "vosk": "vosk", "soundfile": "soundfile"}


class ModelError(Exception):
    pass


class ModelMissing(ModelError):
    """Raised when a stage needs a model nobody installed. The message is the whole
    point: it names the command that fixes it, because this is the one error the
    operator meets before they know the manager exists."""

    def __init__(self, spec: ModelSpec):
        self.spec = spec
        super().__init__(
            f"model '{spec.id}' ({spec.label}, {human_size(spec.size)}, {spec.license}) "
            f"is not installed — run `slopgen models install {spec.id}` "
            f"or use the TUI's model manager"
        )


class _Stalled(Exception):
    pass


# -- progress -------------------------------------------------------------

# (file label, bytes done, bytes total, note) — total 0 while unknown
Progress = Callable[[str, int, int, str], None]


def _noop(_label: str, _done: int, _total: int, _note: str) -> None:
    pass


# -- the transfer ---------------------------------------------------------


def _stream_to(url: str, part: Path, total_hint: int, label: str,
               progress: Progress) -> None:
    """Fetch `url` into `part`, resuming whatever is already there.

    Raises after :data:`MAX_ATTEMPTS` drops; leaves the partial file in place so the
    next call continues rather than restarts."""
    last_err: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True,
                timeout=httpx.Timeout(connect=30.0, read=STALL_WINDOW_S, write=30.0, pool=30.0),
            ) as r:
                if have and r.status_code == 200:
                    # server ignored the range — start over rather than append garbage
                    have = 0
                    part.unlink(missing_ok=True)
                elif r.status_code == 416:  # already complete
                    return
                r.raise_for_status()
                total = have + int(r.headers.get("Content-Length") or 0)
                if total <= have:
                    total = total_hint
                mark_t, mark_b, done = time.monotonic(), have, have
                with open(part, "ab" if have else "wb") as f:
                    for chunk in r.iter_bytes(CHUNK):
                        f.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - mark_t >= STALL_WINDOW_S:
                            if (done - mark_b) / (now - mark_t) < MIN_BYTES_PER_S:
                                raise _Stalled(
                                    f"{(done - mark_b) / (now - mark_t):.0f} B/s over "
                                    f"{now - mark_t:.0f}s"
                                )
                            mark_t, mark_b = now, done
                            progress(label, done, total, "")
                        elif done % (1 << 20) < CHUNK:
                            progress(label, done, total, "")
            return
        except (_Stalled, httpx.HTTPError, OSError) as e:
            last_err = e
            got = part.stat().st_size if part.exists() else 0
            progress(label, got, total_hint,
                     f"reconnecting ({type(e).__name__}) — resuming from {human_size(got)}")
            time.sleep(min(3.0 + attempt, 15.0))
    raise ModelError(f"could not download {url} after {MAX_ATTEMPTS} attempts: {last_err}")


def _safetensors_ok(path: Path) -> bool:
    """Does this file's own tensor map account for exactly its length?"""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            (header_len,) = struct.unpack("<Q", f.read(8))
            if header_len <= 0 or 8 + header_len > size:
                return False
            header = json.loads(f.read(header_len))
        end = max(
            (v["data_offsets"][1] for v in header.values()
             if isinstance(v, dict) and "data_offsets" in v),
            default=0,
        )
        return 8 + header_len + end == size
    except (OSError, ValueError, struct.error, KeyError):
        return False


def _verify(path: Path, spec_file: ModelFile) -> None:
    if path.suffix == ".safetensors" and not _safetensors_ok(path):
        raise ModelError(f"{path.name} is incomplete or corrupt (tensor map does not "
                         f"match the file length) — delete it and install again")
    if spec_file.size and path.stat().st_size != spec_file.size:
        # not fatal: an upstream re-upload is legitimate. Say so and move on.
        pass


# -- the store ------------------------------------------------------------


class ModelStore:
    """Everything installed under one root (``paths.models``)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- queries ----------------------------------------------------------

    def path(self, model_id: str) -> Path:
        return self.root / model_id

    def is_installed(self, model_id: str) -> bool:
        return (self.path(model_id) / MARKER).exists()

    def installed(self) -> list[str]:
        return sorted(m for m in CATALOG if self.is_installed(m))

    def info(self, model_id: str) -> dict:
        try:
            return json.loads((self.path(model_id) / MARKER).read_text())
        except (OSError, ValueError):
            return {}

    def disk_size(self, model_id: str) -> int:
        d = self.path(model_id)
        if not d.is_dir():
            return 0
        return sum(p.stat().st_size for p in d.rglob("*") if p.is_file())

    def require(self, model_id: str) -> Path:
        """The folder, or a :class:`ModelMissing` that tells the operator what to run."""
        spec = get(model_id)
        if not self.is_installed(model_id):
            raise ModelMissing(spec)
        return self.path(model_id)

    # -- pip packages -----------------------------------------------------

    @staticmethod
    def has_cuda_device() -> bool:
        """Is there a GPU worth installing CUDA wheels for?

        Asked of the SYSTEM rather than of torch, because torch is the thing being
        installed. Both signals are cheap and neither loads a driver: the device nodes
        the kernel module creates, and the tool that ships with it."""
        if any(Path("/dev").glob("nvidia[0-9]*")):
            return True
        return shutil.which("nvidia-smi") is not None

    @classmethod
    def _pip_index(cls, spec: ModelSpec) -> str:
        """The alternative wheel index for this model, if it wants one and this
        machine qualifies. Empty means plain PyPI."""
        if spec.cpu_index and not cls.has_cuda_device():
            return spec.cpu_index
        return ""

    @staticmethod
    def missing_packages(spec: ModelSpec) -> list[str]:
        """Requirements that are not importable. Checked by import rather than by
        asking pip, because that is the question the engine will actually ask."""
        out = []
        for req in spec.packages:
            name = req.split(">=")[0].split("==")[0].split("[")[0].strip()
            if find_spec(_IMPORT_NAME.get(name, name.replace("-", "_"))) is None:
                out.append(req)
        return out

    def install_packages(self, spec: ModelSpec, progress: Progress = _noop) -> None:
        """`pip install` the model's requirements into the running interpreter's env.
        A separate step on purpose — torch is a bigger download than most models."""
        missing = self.missing_packages(spec)
        if not missing:
            return
        index = self._pip_index(spec)
        note = f"installing {', '.join(missing)}"
        if index:
            note += " (CPU wheels — no CUDA device here)"
        progress("pip", 0, 0, note)
        cmd = [sys.executable, "-m", "pip", "install"]
        if index:
            # --extra-index-url, not --index-url: the CPU index carries torch and
            # nothing else, and the other requirements still have to come from PyPI
            cmd += ["--extra-index-url", index]
        proc = subprocess.run(cmd + list(missing), capture_output=True, text=True)
        if proc.returncode != 0:
            raise ModelError(
                f"pip install {' '.join(missing)} failed:\n{proc.stderr.strip()[-2000:]}"
            )
        progress("pip", 0, 0, f"installed {', '.join(missing)}")

    # -- install / remove -------------------------------------------------

    def install(self, model_id: str, progress: Progress = _noop,
                with_packages: bool = True) -> Path:
        spec = get(model_id)
        dest = self.path(model_id)
        dest.mkdir(parents=True, exist_ok=True)
        for f in spec.files:
            target = dest / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and (not f.size or target.stat().st_size == f.size):
                progress(f.path, f.size, f.size, "already here")
                continue
            part = target.with_name(target.name + ".part")
            progress(f.path, part.stat().st_size if part.exists() else 0, f.size, "")
            _stream_to(f.url, part, f.size, f.path, progress)
            os.replace(part, target)
            _verify(target, f)
            progress(f.path, f.size or target.stat().st_size, f.size, "done")
        if spec.unpack == "zip":
            self._unpack_zip(spec, dest, progress)
        if with_packages:
            self.install_packages(spec, progress)
        (dest / MARKER).write_text(json.dumps({
            "id": spec.id, "label": spec.label, "license": spec.license,
            "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=1))
        return dest

    def _unpack_zip(self, spec: ModelSpec, dest: Path, progress: Progress) -> None:
        for f in spec.files:
            archive = dest / f.path
            if archive.suffix != ".zip" or not archive.exists():
                continue
            progress(f.path, 0, 0, "unpacking")
            with zipfile.ZipFile(archive) as z:
                names = [n for n in z.namelist() if not n.startswith("/") and ".." not in n]
                roots = {n.split("/", 1)[0] for n in names}
                strip = spec.strip_root and len(roots) == 1
                for n in names:
                    rel = n.split("/", 1)[1] if strip and "/" in n else n
                    if not rel or rel.endswith("/"):
                        continue
                    out = dest / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(n) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            archive.unlink()

    def remove(self, model_id: str) -> Path:
        get(model_id)  # refuse to delete a folder we do not recognise
        d = self.path(model_id)
        if d.is_dir():
            shutil.rmtree(d)
        return d
