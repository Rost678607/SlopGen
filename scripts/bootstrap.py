#!/usr/bin/env python3
"""What `nix-shell` does for NixOS, done by hand for everywhere else.

`shell.nix` is not a build system — it is a list of things that have to exist before
the first frame can be rendered: a Python 3.12, a virtualenv with the wheels in it,
ffmpeg on PATH, and a `LD_LIBRARY_PATH` that only NixOS needs. On Debian, macOS or
Windows the list is shorter but nobody writes it down, so it gets rediscovered one
traceback at a time. This writes it down and then does it.

It is called by `slopgen.sh` and `slopgen.bat`, which know only how to find *a* Python;
everything that needs judgement is here, once, instead of twice in two shell dialects.

Three modes:

    python scripts/bootstrap.py --run -- web      # ensure, then run `slopgen web`
    python scripts/bootstrap.py --setup           # ensure everything, run nothing
    python scripts/bootstrap.py --check           # report, change nothing

The ensure step is cheap on the second run: what was installed is recorded against a
hash of `requirements.txt` and `pyproject.toml`, so the usual path through here is a
hash comparison and a `which`.

Deliberately stdlib-only and deliberately written for an old Python: it is the one file
that has to run BEFORE the environment is right, including under the 3.11 that Debian
12 calls `python3`, where its job is to explain the problem rather than crash on a
syntax it does not know.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_PY = (3, 12)
VENV = Path(os.environ.get("SLOPGEN_VENV") or (ROOT / ".venv"))
TOOLS = ROOT / ".tools"
FFMPEG_BIN = TOOLS / "ffmpeg" / "bin"
STAMP = VENV / "slopgen-bootstrap.json"
WINDOWS = os.name == "nt"

# A static build, fetched when the system has no ffmpeg and no package manager we are
# allowed to use. BtbN's builds are the ones that carry everything this project asks
# of ffmpeg — libass for the subtitles, freetype and harfbuzz for `drawtext`, and
# ffprobe and ffplay beside ffmpeg rather than instead of it.
FFMPEG_RELEASE = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/latest"
FFMPEG_FALLBACK = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-{plat}-gpl{ext}"

# How each platform's own package manager installs ffmpeg, if the operator would rather
# have one in the system than a copy in the repo.
PACKAGE_MANAGERS = (
    ("winget", ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--source", "winget"]),
    ("brew", ["brew", "install", "ffmpeg"]),
    ("apt-get", ["sudo", "apt-get", "install", "-y", "ffmpeg"]),
    ("dnf", ["sudo", "dnf", "install", "-y", "ffmpeg"]),
    ("pacman", ["sudo", "pacman", "-S", "--needed", "--noconfirm", "ffmpeg"]),
    ("zypper", ["sudo", "zypper", "install", "-y", "ffmpeg"]),
    ("apk", ["sudo", "apk", "add", "ffmpeg"]),
    ("choco", ["choco", "install", "-y", "ffmpeg"]),
)

ASSUME_YES = False


# -- talking ----------------------------------------------------------------

def say(message):
    # type: (str) -> None
    sys.stderr.write("slopgen: " + message + "\n")
    sys.stderr.flush()


def warn(message):
    # type: (str) -> None
    say("warning: " + message)


class Fatal(Exception):
    """Something the operator has to decide or install before this can go on."""


def ask(question, default=True):
    # type: (str, bool) -> bool
    """Yes or no, with --yes and a non-interactive terminal both counting as answers."""
    if ASSUME_YES:
        say(question + " — yes (--yes)")
        return True
    if not sys.stdin or not sys.stdin.isatty():
        return False
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        answer = input("slopgen: " + question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return default
    return answer[:1] in ("y", "д", "1")


def run(cmd, **kwargs):
    # type: (list, object) -> int
    """Run something, letting it print, and return its exit code."""
    say("$ " + " ".join(str(c) for c in cmd))
    return subprocess.call([str(c) for c in cmd], **kwargs)  # type: ignore[arg-type]


# -- the interpreter --------------------------------------------------------

def _version_of(cmd):
    # type: (list) -> tuple
    """The (major, minor) of an interpreter named by a command, or () if it is not one."""
    try:
        out = subprocess.check_output(
            [str(c) for c in cmd] + ["-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            stderr=subprocess.DEVNULL, universal_newlines=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    try:
        major, minor = out.strip().split(".")[:2]
        return (int(major), int(minor))
    except ValueError:
        return ()


def _candidates():
    # type: () -> list
    """Every way this machine might name a new enough Python, best first.

    Oldest first, which is not the obvious order: the newest interpreter is the one
    least likely to have a wheel waiting for every dependency, and a venv built on it
    spends twenty minutes compiling pydantic-core before failing on something else.
    3.12 is the floor this project asks for and the version its wheels all exist for."""
    names = ["python3.12", "python3.13", "python3.14"]
    found = []
    if sys.version_info[:2] == MIN_PY:
        found.append([sys.executable])
    for name in names:
        where = shutil.which(name)
        if where:
            found.append([where])
    if WINDOWS and shutil.which("py"):
        # The Windows launcher, which knows about interpreters that are not on PATH.
        found.extend([["py", "-3.12"], ["py", "-3.13"], ["py", "-3.14"]])
    found.append([sys.executable])
    for name in ("python3", "python"):
        where = shutil.which(name)
        if where:
            found.append([where])
    return found


def host_python():
    # type: () -> list
    """A command that runs a Python new enough to build the venv with.

    `uv` is asked last and only if it is already installed: it can fetch a standalone
    CPython in a few seconds, which on a Debian whose `python3` is 3.11 is by far the
    shortest road to 3.12 — but installing uv itself is the operator's decision, not
    something a wrapper script should do behind their back."""
    for cmd in _candidates():
        if _version_of(cmd) >= MIN_PY:
            return cmd
    if shutil.which("uv"):
        say("no Python %d.%d on this machine — asking uv for one" % MIN_PY)
        if run(["uv", "python", "install", "%d.%d" % MIN_PY]) == 0:
            try:
                out = subprocess.check_output(
                    ["uv", "python", "find", "%d.%d" % MIN_PY],
                    universal_newlines=True, timeout=60).strip()
            except (OSError, subprocess.SubprocessError):
                out = ""
            if out and _version_of([out]) >= MIN_PY:
                return [out]
    raise Fatal(
        "no Python %d.%d or newer found (this one is %d.%d).\n"
        "  Install one, then run this again:\n"
        "    Windows   winget install Python.Python.3.12\n"
        "    macOS     brew install python@3.12\n"
        "    Debian    sudo apt install python3.12 python3.12-venv\n"
        "    Fedora    sudo dnf install python3.12\n"
        "    anywhere  install uv (https://astral.sh/uv) and run this again — it will\n"
        "              fetch a private 3.12 without touching the system Python"
        % (MIN_PY + sys.version_info[:2])
    )


def venv_python():
    # type: () -> Path
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def ensure_venv():
    # type: () -> Path
    """The venv's interpreter, creating the venv if it is not there yet."""
    vpy = venv_python()
    if vpy.exists():
        return vpy
    host = host_python()
    say("creating %s" % VENV)
    if run(host + ["-m", "venv", str(VENV)]) != 0:
        raise Fatal(
            "could not create the virtualenv. On Debian/Ubuntu the venv module is a\n"
            "  separate package: sudo apt install python3-venv (or python3.12-venv)")
    if not vpy.exists():
        raise Fatal("the virtualenv was created but has no interpreter at %s" % vpy)
    run([str(vpy), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "wheel"])
    return vpy


# -- the wheels -------------------------------------------------------------

def deps_fingerprint(vpy):
    # type: (Path) -> str
    """What the installed set depends on: the two dependency files and the interpreter.

    The interpreter is in there because a venv rebuilt on a new minor version needs its
    wheels again, and the alternative to noticing that is an import error weeks later."""
    digest = hashlib.sha256()
    for name in ("requirements.txt", "pyproject.toml"):
        path = ROOT / name
        if path.exists():
            digest.update(path.read_bytes())
    digest.update(str(_version_of([str(vpy)])).encode())
    return digest.hexdigest()


def deps_current(vpy):
    # type: (Path) -> bool
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return stamp.get("deps") == deps_fingerprint(vpy)


def ensure_deps(vpy, force=False):
    # type: (Path, bool) -> None
    if deps_current(vpy) and not force:
        return
    say("installing Python dependencies (this is the slow part, once)")
    if run([str(vpy), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]) != 0:
        raise Fatal("pip could not install requirements.txt — the output above says why")
    if run([str(vpy), "-m", "pip", "install", "-e", str(ROOT)]) != 0:
        raise Fatal("pip could not install the slopgen package itself")
    STAMP.write_text(json.dumps({"deps": deps_fingerprint(vpy)}, indent=1), encoding="utf-8")


# -- ffmpeg -----------------------------------------------------------------

def ffmpeg_where():
    # type: () -> Path | None
    """Where ffmpeg AND ffprobe both are, counting the copy in `.tools`.

    Both, because ffprobe is not a nicety here: every asset's duration and every
    picture's dimensions are an ffprobe call, and a build that ships only `ffmpeg.exe`
    fails at the first clip rather than at startup."""
    for directory in (FFMPEG_BIN, None):
        path = str(directory) if directory else None
        found = [shutil.which(name, path=path) for name in ("ffmpeg", "ffprobe")]
        if all(found):
            return Path(found[0]).parent  # type: ignore[arg-type]
    return None


def _ffmpeg_asset():
    # type: () -> tuple
    """(platform token, archive extension) for a static build, or () if there is none.

    No macOS: BtbN does not build for it, and a Mac has `brew install ffmpeg` — which
    is one line and keeps the binary where the rest of the system can see it."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "win32":
        return ("winarm64" if arm else "win64", ".zip")
    if sys.platform.startswith("linux"):
        return ("linuxarm64" if arm else "linux64", ".tar.xz")
    return ()


def _pick_release_asset(plat, ext):
    # type: (str, str) -> tuple
    """(url, name) of the newest non-shared GPL build for this platform.

    Asked of the release rather than guessed, because the names carry version numbers
    that change — but a guess is kept for the case where the API is rate-limited, which
    for an unauthenticated request is an ordinary afternoon."""
    import urllib.request

    try:
        with urllib.request.urlopen(FFMPEG_RELEASE, timeout=30) as response:
            release = json.loads(response.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — any failure here just means "guess"
        warn("could not read the ffmpeg release list (%s) — using the master build" % e)
        return (FFMPEG_FALLBACK.format(plat=plat, ext=ext), "ffmpeg-master-latest-%s-gpl%s" % (plat, ext))

    best = None
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        if "-%s-gpl" % plat not in name or not name.endswith(ext) or "shared" in name:
            continue
        # a numbered release (ffmpeg-n9.0-latest-…) beats the rolling master build
        rank = (0.0,)
        if name.startswith("ffmpeg-n"):
            try:
                rank = (float(name.split("-")[1][1:]),)
            except (IndexError, ValueError):
                rank = (0.0,)
        if best is None or rank > best[0]:
            best = (rank, asset.get("browser_download_url"), name)
    if best is None:
        warn("no %s build in the release — using the master build" % plat)
        return (FFMPEG_FALLBACK.format(plat=plat, ext=ext), "ffmpeg-master-latest-%s-gpl%s" % (plat, ext))
    return (best[1], best[2])


def _checksum(name):
    # type: (str) -> str
    """The published sha256 for one asset, or "" if the list cannot be had."""
    import urllib.request

    url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name:
            return parts[0]
    return ""


def _download(url, dest):
    # type: (str, Path) -> str
    """Fetch `url` into `dest`, returning the sha256 of what actually arrived."""
    import urllib.request

    say("downloading %s" % url)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        step = 0
        with open(str(dest), "wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done - step >= (16 << 20):
                    step = done
                    if total:
                        sys.stderr.write("\r  %d%% of %d MiB" % (done * 100 // total, total >> 20))
                    else:
                        sys.stderr.write("\r  %d MiB" % (done >> 20))
                    sys.stderr.flush()
    sys.stderr.write("\r  done, %d MiB\n" % (done >> 20))
    return digest.hexdigest()


def _extract_bin(archive, into):
    # type: (Path, Path) -> None
    """Pull just the `bin/` of the archive out, flat, into `into`.

    Just the bin: the archive is a whole distribution — headers, licences, a `doc`
    folder — and what is wanted is three executables."""
    into.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(str(archive)) as zf:
            for member in zf.namelist():
                if "/bin/" in member and not member.endswith("/"):
                    target = into / Path(member).name
                    with zf.open(member) as src, open(str(target), "wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(str(archive), "r:*") as tf:
            for member in tf.getmembers():
                if "/bin/" in member.name and member.isfile():
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    target = into / Path(member.name).name
                    with extracted as src, open(str(target), "wb") as dst:
                        shutil.copyfileobj(src, dst)
    for path in into.iterdir():
        if not WINDOWS:
            path.chmod(0o755)


def install_ffmpeg_local():
    # type: () -> bool
    """Fetch a static ffmpeg into `.tools/ffmpeg`. No root, no package manager."""
    asset = _ffmpeg_asset()
    if not asset:
        warn("no static build is published for %s — install ffmpeg with the system's "
             "package manager (macOS: brew install ffmpeg)" % sys.platform)
        return False
    plat, ext = asset
    url, name = _pick_release_asset(plat, ext)
    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / name
    got = _download(url, archive)
    want = _checksum(name)
    if want and want != got:
        archive.unlink()
        raise Fatal("the ffmpeg archive does not match its published sha256 — refusing "
                    "to unpack it. Try again, or install ffmpeg some other way.")
    if not want:
        warn("could not verify the download's checksum (the list was unreachable)")
    say("unpacking into %s" % FFMPEG_BIN)
    _extract_bin(archive, FFMPEG_BIN)
    archive.unlink()
    return ffmpeg_where() is not None


def install_ffmpeg_system():
    # type: () -> bool
    """Let the platform's package manager do it, if there is one and it is wanted."""
    for binary, command in PACKAGE_MANAGERS:
        if not shutil.which(binary):
            continue
        if not ask("install ffmpeg with %s?" % binary, default=True):
            return False
        return run(command) == 0 and ffmpeg_where() is not None
    return False


def ensure_ffmpeg():
    # type: () -> Path | None
    """ffmpeg and ffprobe, wherever they end up living."""
    where = ffmpeg_where()
    if where:
        return where
    say("ffmpeg (or ffprobe) is missing — it is the assembly engine, nothing renders "
        "without it")
    asset = _ffmpeg_asset()
    if asset:
        size = "about 150 MiB"
        if ASSUME_YES or ask("download a private static build into .tools/ffmpeg "
                             "(%s, no admin rights needed)?" % size, default=True):
            if install_ffmpeg_local():
                return ffmpeg_where()
    if install_ffmpeg_system():
        return ffmpeg_where()
    raise Fatal(
        "no ffmpeg. Install it and run this again:\n"
        "    Windows   winget install Gyan.FFmpeg\n"
        "    macOS     brew install ffmpeg\n"
        "    Debian    sudo apt install ffmpeg\n"
        "    Arch      sudo pacman -S ffmpeg\n"
        "  …or re-run with --yes to fetch a private copy into .tools/ffmpeg")


# -- the rest of the list ---------------------------------------------------

def seed_files():
    # type: () -> None
    """Copy the two templates into place, once, so there is something to edit."""
    for template, real in ((".env.example", ".env"),
                           ("configs/slopgen.toml.example", "configs/slopgen.toml")):
        src, dst = ROOT / template, ROOT / real
        if src.exists() and not dst.exists():
            shutil.copyfile(str(src), str(dst))
            say("wrote %s from %s — fill in what you use" % (real, template))


def soft_checks(vpy):
    # type: (Path) -> None
    """The things that are not fatal but are worth knowing about before a four-hour run.

    Each of these costs a feature rather than the program: a missing font costs the
    typeface and not the subtitles, a missing `sox` costs one voice engine out of five,
    a missing `cloudflared` costs the Mini App's public address and not the bot."""
    if not WINDOWS and not shutil.which("fc-match"):
        warn("no fontconfig (fc-match): fonts will be looked up by file name instead. "
             "Debian: sudo apt install fontconfig fonts-dejavu-core")
    face = ""
    try:
        face = subprocess.check_output(
            [str(vpy), "-c",
             "from slopgen import typeface;print(typeface.find('DejaVu Sans') or '')"],
            cwd=str(ROOT), universal_newlines=True, timeout=120,
            stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if face:
        say("subtitle font: %s" % face)
    else:
        warn("nothing on this machine answers to \"DejaVu Sans\" — subtitles will be "
             "drawn in whatever ffmpeg substitutes. Put a .ttf in assets/fonts/, or "
             "set [subtitles].font in configs/slopgen.toml to a family you do have")
    if not shutil.which("sox"):
        warn("no sox: only the local Qwen voice engine wants it (`slopgen models`); "
             "edge-tts, Azure and the API engines do not")
    if not shutil.which("cloudflared"):
        warn("no cloudflared: `slopgen bot` will serve the panel locally but cannot "
             "give the Telegram Mini App a public https address")


# -- putting it together ----------------------------------------------------

def child_env(ffmpeg_dir):
    # type: (Path | None) -> dict
    """The environment slopgen itself is run in — the other half of what shell.nix does.

    UTF-8 mode is the one that matters and the one nobody expects: Windows still
    defaults text files to the ANSI code page, every script, checkpoint and metadata
    file this project writes is Russian as often as not, and a cp1251 default turns the
    first emoji in a title into a crash halfway through a run."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = env.get("PYTHONUTF8") or "1"
    env["PYTHONIOENCODING"] = env.get("PYTHONIOENCODING") or "utf-8"
    if ffmpeg_dir and str(ffmpeg_dir) not in env.get("PATH", ""):
        env["PATH"] = str(ffmpeg_dir) + os.pathsep + env.get("PATH", "")
    fonts = ROOT / "assets" / "fonts"
    if fonts.is_dir() and not env.get("SLOPGEN_FONTS"):
        env["SLOPGEN_FONTS"] = str(fonts)
    return env


def ensure(full=False):
    # type: (bool) -> tuple
    """Everything in the list. Returns (venv python, ffmpeg directory or None)."""
    if full and Path("/etc/NIXOS").exists():
        warn("this is NixOS, where `nix-shell` does all of this and does it better — "
             "see the Nix section of the README. Carrying on anyway.")
    vpy = ensure_venv()
    ensure_deps(vpy, force=full)
    seed_files()
    where = ensure_ffmpeg()
    if full:
        soft_checks(vpy)
    return (vpy, where)


def _packages_state(vpy):
    # type: (Path) -> str
    """What the venv has, in a sentence.

    A venv without a stamp is not necessarily a venv without packages: on NixOS it was
    filled by `nix-shell` and `pip install -e .` by hand, and telling its owner to run
    --setup would be telling them to undo that."""
    if deps_current(vpy):
        return "installed and current"
    try:
        subprocess.check_call([str(vpy), "-c", "import slopgen"], cwd=str(ROOT),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=120)
        return "installed, but not by this script (no stamp — fine)"
    except (OSError, subprocess.SubprocessError):
        return "missing or out of date (run --setup)"


def check():
    # type: () -> int
    """Report, change nothing. The answer to "why does it not work on my machine"."""
    print("repository   %s" % ROOT)
    print("this python  %d.%d (%s)" % (sys.version_info[0], sys.version_info[1], sys.executable))
    vpy = venv_python()
    if vpy.exists():
        version = _version_of([str(vpy)])
        ok = version >= MIN_PY
        print("venv         %s — python %s%s" % (
            vpy, ".".join(str(v) for v in version) or "?", "" if ok else "  TOO OLD"))
        print("packages     %s" % _packages_state(vpy))
    else:
        print("venv         not created yet (run --setup)")
    where = ffmpeg_where()
    print("ffmpeg       %s" % (where or "NOT FOUND (run --setup)"))
    print("fontconfig   %s" % (shutil.which("fc-match") or "absent — names resolved by file"))
    print("sox          %s" % (shutil.which("sox") or "absent — only the local Qwen voice wants it"))
    print("cloudflared  %s" % (shutil.which("cloudflared") or "absent — no Mini App address"))
    print("config       %s" % ("configs/slopgen.toml" if (ROOT / "configs/slopgen.toml").exists()
                                else "not written yet — --setup copies the example"))
    print("keys         %s" % (".env" if (ROOT / ".env").exists()
                               else "no .env yet — --setup copies the example"))
    if vpy.exists() and "missing" not in _packages_state(vpy) and where:
        print("\nready.")
        return 0
    print("\nnot ready — run: %s --setup" % _self_command())
    return 1


def _self_command():
    # type: () -> str
    return "slopgen.bat" if WINDOWS else "./slopgen.sh"


def main(argv):
    # type: (list) -> int
    global ASSUME_YES

    parser = argparse.ArgumentParser(
        prog="bootstrap.py", add_help=True,
        description="Prepare the environment slopgen needs, then optionally run it.")
    parser.add_argument("--run", action="store_true",
                        help="after preparing, run slopgen with the arguments after --")
    parser.add_argument("--setup", action="store_true",
                        help="prepare everything and re-check the dependencies, run nothing")
    parser.add_argument("--check", action="store_true",
                        help="report what is and is not in place, change nothing")
    parser.add_argument("--yes", action="store_true",
                        help="answer yes to everything, including the ffmpeg download")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    ASSUME_YES = args.yes or os.environ.get("SLOPGEN_YES", "") not in ("", "0")

    if args.check:
        return check()
    try:
        vpy, where = ensure(full=args.setup)
    except Fatal as e:
        say("cannot continue: %s" % e)
        return 1
    if args.setup:
        say("ready. Run it with: %s web  (or %s --help)"
            % (_self_command(), _self_command()))
        return 0
    if not args.run:
        return 0

    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    cmd = [str(vpy), "-m", "slopgen"] + rest
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=child_env(where))
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            # Ctrl-C reached the child too — it is the one that has to decide what to
            # do about it, and this process' only remaining job is its exit code.
            continue


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
