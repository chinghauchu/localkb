import json
import subprocess
import sys
from pathlib import Path

from localkb.cli import run
from tests.corpus import write_corpus


def test_cli_init_update_search_and_ask(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    write_corpus(folder)
    assert run(["init", str(folder), "--embedder", "hash"]) == 0
    assert run(["update", str(folder)]) == 0
    assert run(["search", "本機儲存", "--folder", str(folder), "--json"]) == 0


def test_cli_search_json_and_ask_without_clipboard(corpus: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(Path("/nonexistent")))
    assert run(["search", "本季營收", "--folder", str(corpus), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["file"] == "reports/quarter.pdf"
    assert payload["results"][0]["page"] == 2

    assert run(["ask", "本季營收", "--copy", "--folder", str(corpus)]) == 0
    captured = capsys.readouterr()
    assert "page 2" in captured.out
    assert "clipboard" in captured.err.lower()


def test_module_help() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "localkb", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "localkb" in completed.stdout
    assert "update" in completed.stdout
