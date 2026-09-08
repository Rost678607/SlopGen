# Working in this repository

## Run everything through `nix-shell`

This is NixOS, and the Python dependencies are manylinux wheels in a pip venv. They
find `libstdc++.so.6` only through the `LD_LIBRARY_PATH` that `shell.nix` exports, so
every command goes through the shell:

```sh
nix-shell --run '.venv/bin/python -m slopgen web'     # the browser GUI
nix-shell --run '.venv/bin/python -m slopgen tui'     # the frozen terminal UI
nix-shell --run '.venv/bin/python scripts/whatever.py'
```

Never `.venv/bin/python …` on its own. The trap is that it *mostly works*: the config
store, the LLM client, edge TTS and the web server itself are pure Python and start
happily outside the shell. What dies is anything with a C extension — numpy, torch,
vosk, soundfile — and it dies late, inside whichever request first reaches it, with
several hundred characters of numpy's advice about how numpy is usually installed. The
sentence that matters is the last one: `libstdc++.so.6: cannot open shared object
file`. That message never means the code is wrong. It means the wrapper is missing.

The shell also supplies ffmpeg, sox and the DejaVu font the subtitles default to.

## Conventions

- **One branch: `main`.** Commit straight to it and push; no feature branches.
- **Commit messages** are imperative and unprefixed, with a body explaining the *why* —
  what was wrong before, and what the change makes possible.
- **READMEs are bilingual**, English section first, Russian second. A change to one has
  to land in both.
- **The Textual TUI (`src/slopgen/tui/`) is frozen.** It keeps working and gets no new
  functionality; new interface work goes to the web GUI. Mechanical edits that keep it
  from breaking — following a constant that moved, carrying a new config field across a
  save — are fine and expected.
