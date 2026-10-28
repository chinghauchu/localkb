"""Write an MCP client config for this folder, keeping a backup of the previous file."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
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

    def format(self) -> str:
        lines = [f"Wrote {self.path}"]
        if self.backup is not None:
            lines.append(f"Backup: {self.backup}")
        lines.append(_RESTART[self.client])
        lines.append("The config points at this folder. If you move the folder, run install again.")
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


def server_command(folder: Path) -> tuple[str, list[str]]:
    folder_arg = str(folder.resolve())
    executable = shutil.which("localkb")
    if executable:
        return executable, ["serve", "--folder", folder_arg]
    return sys.executable, ["-m", "localkb", "serve", "--folder", folder_arg]


def server_spec(folder: Path, *, client: str) -> dict[str, object]:
    command, args = server_command(folder)
    if client == "vscode":
        return {"type": "stdio", "command": command, "args": args}
    return {"command": command, "args": args}


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
        backup = path.with_name(path.name + ".bak")
        shutil.copy2(path, backup)
    if servers is None:
        servers = {}
        data[key] = servers
    servers["localkb"] = server_spec(folder, client=client)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, data)
    return InstallResult(path=path, backup=backup, client=client)


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
