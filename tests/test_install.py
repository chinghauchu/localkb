import json
import sys
from pathlib import Path

import pytest

from localkb.errors import LocalKBError
from localkb.install import config_path_for, install_client, server_command
from localkb.snapshot import atomic_write_json


def test_config_paths_follow_each_client(tmp_path: Path) -> None:
    home = tmp_path / "home"
    assert config_path_for("cursor", home=home, platform="linux") == home / ".cursor/mcp.json"
    assert config_path_for("gemini", home=home, platform="linux") == home / ".gemini/settings.json"
    assert config_path_for("claude", home=home, platform="linux") == (
        home / ".config/Claude/claude_desktop_config.json"
    )
    assert config_path_for("claude", home=home, platform="darwin") == (
        home / "Library/Application Support/Claude/claude_desktop_config.json"
    )
    assert config_path_for("claude", home=home, platform="win32") == (
        home / "AppData/Roaming/Claude/claude_desktop_config.json"
    )
    assert config_path_for("vscode", home=home, platform="linux") == (
        home / ".config/Code/User/mcp.json"
    )


def test_install_merges_and_backs_up(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "echo"}}}, indent=2),
        encoding="utf-8",
    )
    result = install_client("cursor", corpus, config_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["mcpServers"]["other"]["command"] == "echo"
    spec = saved["mcpServers"]["localkb"]
    assert spec["args"][:1] == ["serve"] or spec["args"][0:3] == ["-m", "localkb", "serve"]
    assert "--folder" in spec["args"]
    assert str(corpus.resolve()) in spec["args"]
    assert result.backup is not None
    assert result.backup.name.startswith("mcp.json.bak-")
    assert result.backup.is_file()
    assert not path.with_name("mcp.json.bak").exists()


def test_vscode_uses_servers_key(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    install_client("vscode", corpus, config_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["servers"]["localkb"]["type"] == "stdio"
    assert "serve" in saved["servers"]["localkb"]["args"]


def test_invalid_json_is_left_unchanged(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LocalKBError, match="not valid JSON"):
        install_client("gemini", corpus, config_path=path)
    assert path.read_text(encoding="utf-8") == "{not json"
    assert not path.with_name("mcp.json.bak").exists()
    assert list(tmp_path.glob("mcp.json.bak*")) == []


def test_backups_do_not_overwrite_and_keep_mode(corpus: Path, tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {"other": {"command": "echo"}}}\n', encoding="utf-8")
    path.chmod(0o600)
    legacy = path.with_name("mcp.json.bak")
    legacy.write_text("keep-me", encoding="utf-8")
    first = install_client("cursor", corpus, config_path=path)
    second = install_client("cursor", corpus, config_path=path)
    assert first.backup is not None and second.backup is not None
    assert first.backup != second.backup
    assert first.backup.is_file() and second.backup.is_file()
    assert first.backup.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(first.backup.read_text(encoding="utf-8"))["mcpServers"]["other"]["command"]
        == "echo"
    )
    assert "localkb" in json.loads(second.backup.read_text(encoding="utf-8"))["mcpServers"]
    assert legacy.read_text(encoding="utf-8") == "keep-me"
    assert path.stat().st_mode & 0o777 == 0o600


def test_atomic_write_preserves_mode(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o640)
    atomic_write_json(path, {"ok": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
    assert path.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".meta.json.*.tmp")) == []


def test_uvx_environment_writes_uvx_command(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "uv-cache"
    executable = cache / "archive-v0" / "abc" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setattr(sys, "executable", str(executable))
    command, args, warning = server_command(corpus)
    assert Path(command).name == "uvx"
    assert args[:3] == ["localkb", "serve", "--folder"]
    assert args[-1] == str(corpus.resolve())
    assert warning is not None
    assert "uv tool install" in warning
