"""Read/write single variables in the project .env file.

Used by the TUI so API keys entered in forms are persisted (gitignored .env),
never written into configs/*.toml.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_PATH = Path(".env")


def set_env_var(name: str, value: str, path: Path = ENV_PATH) -> None:
    """Update or append `name=value`, preserving all other lines; also updates os.environ."""
    line = f"{name}={value}"
    if path.exists():
        text = path.read_text()
        pattern = re.compile(rf"^{re.escape(name)}=.*$", flags=re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            text = text.rstrip("\n") + ("\n" if text.strip() else "") + line + "\n"
    else:
        text = line + "\n"
    path.write_text(text)
    os.environ[name] = value
