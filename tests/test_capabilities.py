import pytest

from roomba_mcp.capabilities import (
    DERIVED,
    UNVERIFIED,
    VERIFIED,
    capability_report,
    experimental_enabled,
    raw_enabled,
    require_experimental,
    require_raw,
    require_writes,
    writes_enabled,
)
from roomba_mcp.errors import ExperimentalRequired, RawDisabled, WritesDisabled


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", " TRUE "])
def test_flags_accept_common_true_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", value)
    monkeypatch.setenv("ROOMBA_ALLOW_RAW", value)
    monkeypatch.setenv("ROOMBA_EXPERIMENTAL", value)
    assert writes_enabled()
    assert raw_enabled()
    assert experimental_enabled()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_flags_reject_other_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", value)
    assert not writes_enabled()


def test_gates_raise_with_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOMBA_ALLOW_WRITES", raising=False)
    monkeypatch.delenv("ROOMBA_ALLOW_RAW", raising=False)
    monkeypatch.delenv("ROOMBA_EXPERIMENTAL", raising=False)
    with pytest.raises(WritesDisabled):
        require_writes()
    with pytest.raises(RawDisabled):
        require_raw()
    with pytest.raises(ExperimentalRequired):
        require_experimental("region clean")


def test_gates_pass_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    monkeypatch.setenv("ROOMBA_ALLOW_RAW", "1")
    monkeypatch.setenv("ROOMBA_EXPERIMENTAL", "1")
    require_writes()
    require_raw()
    require_experimental("region clean")


def test_capability_report_tiers() -> None:
    report = capability_report()
    assert report["status"]["tier"] == VERIFIED
    assert report["mission_history"]["tier"] == DERIVED
    assert report["start_clean_parameterized"]["tier"] == UNVERIFIED
    assert report["raw_publish"]["transports"]["cloud"] == "unsupported"
