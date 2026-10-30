import json
from pathlib import Path

import pytest

from roomba_mcp.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_token_writes_private_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ROOMBA_CONFIG_DIR", str(tmp_path))
    assert main(["token"]) == 0
    body = json.loads(capsys.readouterr().out)
    token = body["token"]
    path = Path(body["token_file"])
    assert path == tmp_path / "http-token"
    assert path.read_text(encoding="utf-8").strip() == token
    assert path.stat().st_mode & 0o777 == 0o600

    # A second call returns the same token unless --force is used.
    assert main(["token"]) == 0
    assert json.loads(capsys.readouterr().out)["token"] == token
    assert main(["token", "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["token"] != token


def test_healthcheck_reports_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ROOMBA_CONFIG_DIR", str(tmp_path))
    assert main(["healthcheck", "--url", "http://127.0.0.1:1/healthz"]) == 1
    assert "cannot reach" in capsys.readouterr().err


def test_serve_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["serve", "--help"])
    assert "--stdio" in capsys.readouterr().out
