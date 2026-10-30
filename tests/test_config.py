import json
import os
import stat
from pathlib import Path

import pytest

from roomba_mcp.config import (
    DEFAULT_HTTP_PORT,
    config_dir,
    is_loopback,
    load_config,
    write_private_bytes,
)
from roomba_mcp.errors import ConfigError


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ROOMBA_CONFIG_DIR",
        "ROOMBA_CONFIG",
        "ROOMBA_BLID",
        "ROOMBA_HOST",
        "ROOMBA_ROBOT_PORT",
        "ROOMBA_TRANSPORT",
        "ROOMBA_HTTP_HOST",
        "ROOMBA_HTTP_PORT",
        "ROOMBA_LOG_LEVEL",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    config = load_config(directory=tmp_path)
    assert config.http.port == DEFAULT_HTTP_PORT
    assert config.transport.mode == "auto"
    assert config.robot.port == 8883
    assert config.directory == tmp_path


def test_config_file_then_env_then_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "config.toml").write_text(
        "[robot]\nblid = 'FROMFILE'\nhost = '10.0.0.1'\n"
        "[http]\nport = 9000\n"
        "[transport]\nmode = 'local'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROOMBA_HOST", "10.0.0.2")
    monkeypatch.setenv("ROOMBA_TRANSPORT", "cloud")
    config = load_config(directory=tmp_path, host="10.0.0.3", http_port=9100)
    assert config.robot.blid == "FROMFILE"
    assert config.robot.host == "10.0.0.3"
    assert config.transport.mode == "cloud"
    assert config.http.port == 9100


def test_blid_falls_back_to_login_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "login.json").write_text(json.dumps({"blid": "ROBOT1"}), encoding="utf-8")
    assert load_config(directory=tmp_path).robot.blid == "ROBOT1"


def test_invalid_transport_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    (tmp_path / "config.toml").write_text("[transport]\nmode = 'carrier-pigeon'\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(directory=tmp_path)


def test_invalid_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(ConfigError):
        load_config(directory=tmp_path, http_port=0)


def test_config_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ROOMBA_CONFIG_DIR", str(tmp_path / "custom"))
    assert config_dir() == tmp_path / "custom"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("ROOMBA_CONFIG_DIR")
    assert config_dir() == tmp_path / "xdg" / "roomba-mcp"


def test_loopback_detection() -> None:
    assert is_loopback("127.0.0.1")
    assert is_loopback("localhost")
    assert is_loopback("::1")
    assert not is_loopback("0.0.0.0")
    assert not is_loopback("192.168.1.10")


def test_private_writes_are_0600(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "secret"
    write_private_bytes(path, b"value")
    assert path.read_bytes() == b"value"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
