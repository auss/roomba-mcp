from pathlib import Path
from typing import Any

import pytest

from roomba_mcp.config import Config, HttpConfig, LoggingConfig, RobotConfig, TransportConfig
from roomba_mcp.errors import TransportTimeout
from roomba_mcp.shadow import ShadowCache
from roomba_mcp.transports.base import WriteResult
from roomba_mcp.transports.manager import TransportManager


class FakeTransport:
    def __init__(
        self,
        name: str,
        current_shadow: str | None,
        responses: dict[Any, dict[str, Any]] | None = None,
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self.name = name
        self.current_shadow = current_shadow
        self.connected = True
        self.started = False
        self.requests: list[Any] = []
        self.writes: list[dict[str, Any]] = []
        self._responses = responses or {}
        self._read_error = read_error
        self._write_error = write_error

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.connected = False

    def request_shadow(self, name: str | None, timeout: float) -> dict[str, Any]:
        self.requests.append(name)
        if self._read_error is not None:
            raise self._read_error
        return self._responses[name]

    def write_command(self, payload: dict[str, Any], timeout: float) -> WriteResult:
        self.writes.append(payload)
        if self._write_error is not None:
            raise self._write_error
        return WriteResult("sent", self.name)

    def write_settings(self, values: dict[str, Any], shadow: str, timeout: float) -> WriteResult:
        if self._write_error is not None:
            raise self._write_error
        return WriteResult("sent", self.name)

    def raw_publish(self, topic: str, payload: str) -> WriteResult:
        return WriteResult("sent", self.name)


def make_manager(
    monkeypatch: pytest.MonkeyPatch,
    transports: dict[str, FakeTransport],
    *,
    mode: str = "auto",
    choose: str = "local",
) -> TransportManager:
    config = Config(
        robot=RobotConfig(blid="B"),
        transport=TransportConfig(mode=mode),
        http=HttpConfig(),
        logging=LoggingConfig(),
        directory=Path("/tmp"),
    )
    manager = TransportManager(config, None, ShadowCache())
    monkeypatch.setattr(manager, "_make", lambda name: transports[name])
    monkeypatch.setattr(manager, "_choose", lambda: choose)
    return manager


def test_read_current_uses_live_shadow_per_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    local = FakeTransport("local", None, {None: {"batPct": 90}})
    manager = make_manager(monkeypatch, {"local": local})
    snapshot = manager.read_current()
    assert snapshot.reported == {"batPct": 90}
    assert snapshot.transport == "local"
    assert local.requests == [None]

    cloud = FakeTransport("cloud", "ro-currentstate", {"ro-currentstate": {"batPct": 80}})
    manager = make_manager(monkeypatch, {"cloud": cloud}, choose="cloud")
    snapshot = manager.read_current()
    assert snapshot.reported == {"batPct": 80}
    assert cloud.requests == ["ro-currentstate"]


def test_read_current_fails_over_to_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    local = FakeTransport("local", None, read_error=TransportTimeout("local down"))
    cloud = FakeTransport("cloud", "ro-currentstate", {"ro-currentstate": {"batPct": 70}})
    manager = make_manager(monkeypatch, {"local": local, "cloud": cloud})
    snapshot = manager.read_current()
    assert snapshot.transport == "cloud"
    assert snapshot.reported == {"batPct": 70}
    assert not local.connected
    assert cloud.started


def test_read_current_serves_stale_cache_when_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ShadowCache()
    cache.update_current({"batPct": 50}, "local")
    config = Config(
        robot=RobotConfig(blid="B"),
        transport=TransportConfig(mode="local"),
        http=HttpConfig(),
        logging=LoggingConfig(),
        directory=Path("/tmp"),
    )
    manager = TransportManager(config, None, cache, snapshot_ttl_s=0.0)
    local = FakeTransport("local", None, read_error=TransportTimeout("down"))
    monkeypatch.setattr(manager, "_make", lambda name: local)
    monkeypatch.setattr(manager, "_choose", lambda: "local")
    snapshot = manager.read_current(refresh=True)
    assert snapshot.stale is True
    assert snapshot.reported == {"batPct": 50}


def test_writes_fail_closed_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    local = FakeTransport("local", None, write_error=TransportTimeout("no ack"))
    cloud = FakeTransport("cloud", "ro-currentstate", {})
    manager = make_manager(monkeypatch, {"local": local, "cloud": cloud})
    result = manager.write_command({"command": "start"})
    assert result.outcome == "unknown"
    assert result.transport == "local"
    assert local.writes == [{"command": "start"}]
    assert cloud.writes == []


def test_read_settings_uses_current_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ShadowCache()
    cache.update_current({"vacHigh": True}, "local")
    config = Config(
        robot=RobotConfig(blid="B"),
        transport=TransportConfig(mode="local"),
        http=HttpConfig(),
        logging=LoggingConfig(),
        directory=Path("/tmp"),
    )
    manager = TransportManager(config, None, cache)
    local = FakeTransport("local", None, {None: {"vacHigh": False}})
    monkeypatch.setattr(manager, "_make", lambda name: local)
    monkeypatch.setattr(manager, "_choose", lambda: "local")
    snapshot = manager.read_settings()
    assert snapshot.reported == {"vacHigh": True}
    assert local.requests == []


def test_read_settings_uses_named_shadow_in_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = FakeTransport("cloud", "ro-currentstate", {"rw-settings": {"vacHigh": False}})
    manager = make_manager(monkeypatch, {"cloud": cloud}, choose="cloud")
    snapshot = manager.read_settings()
    assert snapshot.reported == {"vacHigh": False}
    assert cloud.requests == ["rw-settings"]


def test_read_classic_local_and_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ShadowCache()
    cache.update_current({"sku": "c975840"}, "local")
    config = Config(
        robot=RobotConfig(blid="B"),
        transport=TransportConfig(mode="local"),
        http=HttpConfig(),
        logging=LoggingConfig(),
        directory=Path("/tmp"),
    )
    manager = TransportManager(config, None, cache)
    local = FakeTransport("local", None, {None: {"sku": "local"}})
    monkeypatch.setattr(manager, "_make", lambda name: local)
    monkeypatch.setattr(manager, "_choose", lambda: "local")
    assert manager.read_classic().reported == {"sku": "c975840"}

    cloud = FakeTransport("cloud", "ro-currentstate", {None: {"cap": [1], "sku": "c975840"}})
    manager = make_manager(monkeypatch, {"cloud": cloud}, choose="cloud")
    snapshot = manager.read_classic()
    assert snapshot.reported == {"cap": [1], "sku": "c975840"}
    assert cloud.requests == [None]


def test_raw_publish_is_local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from roomba_mcp.errors import Unsupported

    cloud = FakeTransport("cloud", "ro-currentstate", {})
    manager = make_manager(monkeypatch, {"cloud": cloud}, choose="cloud")
    with pytest.raises(Unsupported):
        manager.raw_publish("cmd", "{}")


def test_health_reports_login_state(monkeypatch: pytest.MonkeyPatch) -> None:
    local = FakeTransport("local", None, {})
    manager = make_manager(monkeypatch, {"local": local})
    health = manager.health()
    assert health["mode"] == "auto"
    assert health["login_present"] is False
    assert health["login_expired"] is None
