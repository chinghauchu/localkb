"""Write an MCP client config for this folder, keeping a backup of the previous file."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from localkb.errors import LocalKBError
from localkb.pipeline import require_kb
from localkb.snapshot import atomic_write_json

CLIENTS = ("claude", "cursor", "vscode", "gemini")

_RESTART = {
    "claude": "Restart Claude Desktop so it reloads MCP servers.",
    "cursor": "Restart Cursor, or reload MCP servers from settings.",
    "vscode": "Reload VS Code so it picks up the MCP server.",
    "gemini": "Restart the Gemini CLI session.",
}


@dataclass(frozen=True)
class InstallResult:
    path: Path
    backup: Path | None
    client: str
    warning: str | None = None

    def format(self) -> str:
        lines = [f"Wrote {self.path}"]
        if self.backup is not None:
            lines.append(f"Backup: {self.backup}")
        lines.append(_RESTART[self.client])
        lines.append("The config points at this folder. If you move the folder, run install again.")
        if self.warning:
            lines.append(self.warning)
        return "\n".join(lines)


def config_path_for(client: str, *, home: Path, platform: str) -> Path:
    system = platform.lower()
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "gemini":
        return home / ".gemini" / "settings.json"
    if client == "claude":
        if system == "darwin":
            return home / "Library/Application Support/Claude/claude_desktop_config.json"
        if system.startswith("win"):
            return home / "AppData/Roaming/Claude/claude_desktop_config.json"
        return home / ".config/Claude/claude_desktop_config.json"
    if client == "vscode":
        if system == "darwin":
            base = home / "Library/Application Support/Code/User"
        elif system.startswith("win"):
            base = home / "AppData/Roaming/Code/User"
        else:
            base = home / ".config/Code/User"
        return base / "mcp.json"
    raise LocalKBError(f"unknown client {client!r}; choose {', '.join(CLIENTS)}")


def server_command(folder: Path) -> tuple[str, list[str], str | None]:
    """Return the MCP command, its args, and a warning when the command is provisional.

    ``uvx`` runs from a temporary environment. Writing that interpreter path into a
    client config breaks as soon as the process exits, so the config calls
    ``uvx localkb serve`` instead.
    """
    folder_arg = str(folder.resolve())
    if _path_in_uv_cache(Path(sys.executable)):
        uvx = shutil.which("uvx") or "uvx"
        warning = (
            "This process is a temporary uvx environment, so the config calls "
            "`uvx localkb serve`. That needs localkb to be available to uvx. "
            "For a command that outlives this process, run "
            "`uv tool install --from <checkout> localkb` and then `localkb install` again."
        )
        return uvx, ["localkb", "serve", "--folder", folder_arg], warning
    executable = shutil.which("localkb")
    if executable and not _path_in_uv_cache(Path(executable)):
        return executable, ["serve", "--folder", folder_arg], None
    return sys.executable, ["-m", "localkb", "serve", "--folder", folder_arg], None


def server_spec(folder: Path, *, client: str) -> tuple[dict[str, object], str | None]:
    command, args, warning = server_command(folder)
    if client == "vscode":
        return {"type": "stdio", "command": command, "args": args}, warning
    return {"command": command, "args": args}, warning


def install_client(
    client: str,
    folder: Path,
    *,
    home: Path | None = None,
    platform: str | None = None,
    config_path: Path | None = None,
) -> InstallResult:
    if client not in CLIENTS:
        raise LocalKBError(f"unknown client {client!r}; choose {', '.join(CLIENTS)}")
    require_kb(folder)
    home = home or Path.home()
    platform = platform or sys.platform
    path = config_path or config_path_for(client, home=home, platform=platform)
    data, existed = _read_config(path)
    key = "servers" if client == "vscode" else "mcpServers"
    servers = data.get(key)
    if servers is not None and not isinstance(servers, dict):
        raise LocalKBError(f"{path} key {key!r} is not a JSON object; it was left unchanged")
    backup: Path | None = None
    if existed:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
    if servers is None:
        servers = {}
        data[key] = servers
    spec, warning = server_spec(folder, client=client)
    servers["localkb"] = spec
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)
    return InstallResult(path=path, backup=backup, client=client, warning=warning)


def _backup_path(path: Path) -> Path:
    """A new backup path. Never reuses ``name.bak`` or an existing timestamped backup."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
    return candidate


def _uv_cache_dir() -> Path:
    override = os.environ.get("UV_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "uv"


def _path_in_uv_cache(path: Path) -> bool:
    try:
        path.resolve().relative_to(_uv_cache_dir().resolve())
    except ValueError:
        return False
    return True


def _read_config(path: Path) -> tuple[dict[str, object], bool]:
    if not path.exists():
        return {}, False
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}, True
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalKBError(f"{path} is not valid JSON; it was left unchanged ({exc})") from exc
    if not isinstance(data, dict):
        raise LocalKBError(f"{path} is not a JSON object; it was left unchanged")
    return data, True
