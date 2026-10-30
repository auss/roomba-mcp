import pytest

from roomba_mcp.errors import InvalidArgument
from roomba_mcp.payloads import (
    INITIATOR,
    command,
    region_clean,
    setting_state,
    validate_passes,
    validate_suction,
)


def test_command_envelope() -> None:
    payload = command("start", now=1000.9)
    assert payload == {"command": "start", "time": 1000, "initiator": INITIATOR}


def test_setting_state_wraps_values() -> None:
    assert setting_state({"vacHigh": True}) == {"state": {"vacHigh": True}}


def test_region_clean_carries_region_params() -> None:
    payload = region_clean(
        pmap_id="map-1",
        regions=["2", "3"],
        passes=2,
        suction="high",
        edge_clean=True,
        now=10,
    )
    assert payload["command"] == "start"
    assert payload["ordered"] == 1
    assert payload["pmap_id"] == "map-1"
    assert payload["regions"][0] == {
        "type": "rid",
        "region_id": "2",
        "params": {"twoPass": True, "noAutoPasses": False, "vacHigh": True, "edgeClean": True},
    }


def test_region_clean_requires_a_region() -> None:
    with pytest.raises(InvalidArgument):
        region_clean(pmap_id="map-1", regions=[])


def test_validate_passes() -> None:
    assert validate_passes(None) is None
    assert validate_passes(1) == 1
    with pytest.raises(InvalidArgument):
        validate_passes(3)


def test_validate_suction() -> None:
    assert validate_suction("normal") == "normal"
    with pytest.raises(InvalidArgument):
        validate_suction("turbo")
