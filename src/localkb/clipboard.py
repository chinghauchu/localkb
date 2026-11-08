"""Copy text with the platform clipboard tools. Returns False when none are available."""

from __future__ import annotations

import shutil
import subprocess

_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("pbcopy", ["pbcopy"]),
    ("wl-copy", ["wl-copy"]),
    ("xclip", ["xclip", "-selection", "clipboard"]),
    ("xsel", ["xsel", "--clipboard", "--input"]),
    ("clip", ["clip"]),
)


def copy_text(text: str) -> bool:
    payload = text.encode("utf-8")
    for binary, command in _COMMANDS:
        if shutil.which(binary) is None:
            continue
        try:
            subprocess.run(
                command,
                input=payload,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        return True
    return False
