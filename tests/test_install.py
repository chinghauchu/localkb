import json
from pathlib import Path

import pytest

from localkb.errors import LocalKBError
from localkb.install import config_path_for, install_client


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
    assert result.backup == path.with_name("mcp.json.bak")
    assert result.backup.is_file()


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
