# Dev environment for slopgen.
# Provides Python 3.12 and ffmpeg; Python deps live in a pip venv (.venv).
# Usage: nix-shell  →  (first time) pip install -r requirements.txt && pip install -e .
{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = with pkgs; [
    python312
    ffmpeg
    sox # qwen-tts imports the `sox` bindings and shouts if the binary is absent
    dejavu_fonts # default subtitle font
  ];

  shellHook = ''
    # Manylinux wheels (pydantic-core etc.) need libgcc/libstdc++ at runtime on NixOS.
    # zlib is here for the wheels `slopgen models install` pulls in later (vosk,
    # torch); the base install only needs libgcc/libstdc++.
    export LD_LIBRARY_PATH=${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.zlib}/lib:$LD_LIBRARY_PATH

    if [ ! -d .venv ]; then
      python3.12 -m venv .venv
      echo "venv created; run: pip install -r requirements.txt && pip install -e ."
    fi
    source .venv/bin/activate
  '';
}
