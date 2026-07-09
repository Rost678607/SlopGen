# Dev environment for slopgen.
# Provides Python 3.12 and ffmpeg; Python deps live in a pip venv (.venv).
# Usage: nix-shell  →  (first time) pip install -r requirements.txt && pip install -e .
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = with pkgs; [
    python312
    ffmpeg
    dejavu_fonts # default subtitle font
  ];

  shellHook = ''
    # Manylinux wheels (pydantic-core etc.) need libgcc/libstdc++ at runtime on NixOS.
    export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:$LD_LIBRARY_PATH

    if [ ! -d .venv ]; then
      python3.12 -m venv .venv
      echo "venv created; run: pip install -r requirements.txt && pip install -e ."
    fi
    source .venv/bin/activate
  '';
}
