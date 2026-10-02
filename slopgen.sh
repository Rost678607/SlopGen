#!/usr/bin/env bash
# One way in, on any Linux, any BSD, and macOS.
#
#   ./slopgen.sh web                 # the browser GUI
#   ./slopgen.sh info ru facts -n 3  # anything the CLI takes
#   ./slopgen.sh --setup             # install everything, run nothing
#   ./slopgen.sh --check             # what is and is not in place
#   ./slopgen.sh --yes web           # and never ask before installing
#
# This script finds a Python and nothing else. Every decision — which interpreter,
# which virtualenv, where ffmpeg comes from — belongs to scripts/bootstrap.py, so that
# it is made the same way here and in slopgen.bat.
#
# On NixOS it steps aside for running slopgen: nix-shell already does all of this, and
# does it better. --check and --setup still answer for themselves, because "what does
# this machine have" is a question worth being able to ask on any machine.
set -e

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)
cd "$here"

# The venv's own interpreter once there is one: it is guaranteed new enough, and after
# the first run this script needs nothing from the system but a shell.
py=""
if [ -x .venv/bin/python ]; then
    py=.venv/bin/python
else
    for candidate in python3.14 python3.13 python3.12 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            py=$candidate
            break
        fi
    done
fi

if [ -z "$py" ]; then
    echo "slopgen: no Python at all on this machine. Install 3.12 or newer:" >&2
    echo "    Debian/Ubuntu  sudo apt install python3.12 python3.12-venv" >&2
    echo "    Fedora         sudo dnf install python3.12" >&2
    echo "    Arch           sudo pacman -S python" >&2
    echo "    macOS          brew install python@3.12" >&2
    exit 1
fi

# --setup and --check are the bootstrap's own business; --yes is a flag it takes, and
# everything else is an argument for slopgen itself.
extra=""
while [ $# -gt 0 ]; do
    case "$1" in
        --setup|--check)
            exec "$py" scripts/bootstrap.py $extra "$@"
            ;;
        --yes)
            extra="$extra --yes"
            shift
            ;;
        *)
            break
            ;;
    esac
done

if [ -z "${SLOPGEN_NO_NIX:-}" ] && [ -e /etc/NIXOS ] && command -v nix-shell >/dev/null 2>&1; then
    # shell.nix supplies the interpreter, ffmpeg, sox, the fonts and the
    # LD_LIBRARY_PATH the manylinux wheels need. Hand it the whole command, quoted.
    exec nix-shell "$here/shell.nix" --run "$(printf '%q ' .venv/bin/python -m slopgen "$@")"
fi

exec "$py" scripts/bootstrap.py $extra --run -- "$@"
