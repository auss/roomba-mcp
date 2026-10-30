import json
import logging
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeLink, FakeRest, load_fixture
from roomba_mcp.config import Config, HttpConfig, LoggingConfig, RobotConfig, TransportConfig
from roomba_mcp.errors import (
    ExperimentalRequired,
    InvalidArgument,
    RawDisabled,
    WritesDisabled,
)
from roomba_mcp.service import RobotService
from roomba_mcp.shadow import extract_reported


def make_service(tmp_path: Path, link: FakeLink | None = None, rest: Any = None) -> RobotService:
    config = Config(
        robot=RobotConfig(blid="0123456789ABCDEF0123456789ABCDEF"),
        transport=TransportConfig(),
        http=HttpConfig(),
        logging=LoggingConfig(),
        directory=tmp_path,
    )
    return RobotService(
        config=config,
        link=link or FakeLink(),
        rest=rest,
        now=lambda: 1000.0,
    )


def loaded_link() -> FakeLink:
    shadows = load_fixture("shadows.json")
    link = FakeLink()
    link.current = extract_reported(shadows["ro-currentstate"]) or {}
    link.classic = extract_reported(shadows["classic"]) or {}
    link.named = {
        "rw-settings": extract_reported(shadows["rw-settings"]) or {},
        "rw-schedule": extract_reported(shadows["rw-schedule"]) or {},
    }
    return link


def test_status_payload() -> None:
    service = make_service(Path("/tmp"), loaded_link())
    body = service.get_status()
    assert body["ok"] is True
    assert body["transport"] == "local"
    assert body["stale"] is False
    assert body["battery_pct"] is not None
    assert body["mission"]["phase"] is not None
    assert body["reported"]["batPct"] == body["battery_pct"]


def test_settings_payload() -> None:
    service = make_service(Path("/tmp"), loaded_link())
    body = service.get_settings()
    assert body["values"]["suction"] == "normal"
    assert body["values"]["multi_pass"] is False
    assert body["values"]["pad_wetness"] == 2
    assert body["values"]["child_lock"] is False


def test_schedule_payload() -> None:
    service = make_service(Path("/tmp"), loaded_link())
    body = service.get_schedule()
    assert body["schedule"]
    assert body["schedule"][0]["cmdStr"]


def test_mission_payload() -> None:
    service = make_service(Path("/tmp"), loaded_link())
    body = service.get_mission()
    assert body["ok"] is True
    assert body["mission_id"] is not None
    assert body["phase"] is not None


def test_robot_info_combines_classic_and_live() -> None:
    service = make_service(Path("/tmp"), loaded_link())
    body = service.get_robot_info()
    assert body["robot"]["sku"] == "c975840"
    assert body["robot"]["cap"] is not None
    assert body["robot"]["name"] == "Roomba Combo"
    assert body["capabilities"]["status"]["tier"] == "verified"
    assert body["safety"]["writes_enabled"] is False
    assert body["errors"] == []


def test_consumables_mapping() -> None:
    rest = FakeRest()
    rest.parts_payload["parts"][0]["counter"] = 5
    service = make_service(Path("/tmp"), rest=rest)
    body = service.get_consumables()
    assert body["num_parts"] == 5
    first = body["parts"][0]
    assert first["part_id"] == "135"
    assert first["needs_attention"] is True
    assert first["last_updated"] is not None
    assert body["parts"][1]["needs_attention"] is False


def test_mission_history_summary() -> None:
    service = make_service(Path("/tmp"), rest=FakeRest())
    body = service.get_mission_history(limit=2)
    assert body["count"] == 2
    mission = body["missions"][0]
    assert mission["done_code"] == "fin"
    assert mission["error"] == {"type": 1, "value": 19}
    assert mission["map_ids"] == ["map-AAAA1111"]


def test_mission_history_limit_validation() -> None:
    service = make_service(Path("/tmp"), rest=FakeRest())
    with pytest.raises(InvalidArgument):
        service.get_mission_history(limit=0)
    with pytest.raises(InvalidArgument):
        service.get_mission_history(limit=101)


def test_map_mapping_and_cache() -> None:
    rest = FakeRest()
    service = make_service(Path("/tmp"), rest=rest)
    body = service.get_map()
    assert body["count"] == 1
    assert body["maps"][0]["name"] == "Main floor"
    assert body["maps"][0]["regions"][0]["name"] == "Hallway"
    # A second read is served from the 60 s cache even if the client changes.
    rest.pmaps_payload = []
    assert service.get_map()["count"] == 1
    assert service.get_map(refresh=True)["count"] == 0


def test_writes_are_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOMBA_ALLOW_WRITES", raising=False)
    service = make_service(Path("/tmp"), loaded_link())
    for call in (service.start_clean, service.pause_clean, service.dock, service.find_robot):
        with pytest.raises(WritesDisabled):
            call()


def test_simple_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link)
    body = service.pause_clean()
    assert body["ok"] is True
    assert body["outcome"] == "sent"
    assert link.commands[-1] == {"command": "pause", "time": 1000, "initiator": "localApp"}


def test_start_clean_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link)
    service.start_clean()
    assert link.commands[-1]["command"] == "start"
    assert "regions" not in link.commands[-1]


def test_start_clean_parameters_need_experimental(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    monkeypatch.delenv("ROOMBA_EXPERIMENTAL", raising=False)
    service = make_service(Path("/tmp"), loaded_link(), rest=FakeRest())
    with pytest.raises(ExperimentalRequired):
        service.start_clean(regions=["2"])


def test_start_clean_parameterized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    monkeypatch.setenv("ROOMBA_EXPERIMENTAL", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link, rest=FakeRest())
    service.start_clean(regions=["2", "3"], passes=2, suction="high")
    payload = link.commands[-1]
    assert payload["pmap_id"] == "map-AAAA1111"
    assert payload["ordered"] == 1
    assert [r["region_id"] for r in payload["regions"]] == ["2", "3"]
    assert payload["regions"][0]["params"]["twoPass"] is True


def test_start_clean_unknown_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    monkeypatch.setenv("ROOMBA_EXPERIMENTAL", "1")
    service = make_service(Path("/tmp"), loaded_link(), rest=FakeRest())
    with pytest.raises(InvalidArgument) as excinfo:
        service.start_clean(regions=["999"])
    assert excinfo.value.detail["maps"]


def test_start_clean_parameters_need_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    monkeypatch.setenv("ROOMBA_EXPERIMENTAL", "1")
    service = make_service(Path("/tmp"), loaded_link(), rest=FakeRest())
    with pytest.raises(InvalidArgument):
        service.start_clean(passes=2)


def test_set_setting_typed_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link)
    service.set_setting("suction", "high")
    assert link.settings_writes[-1] == ({"vacHigh": True}, "rw-settings")
    service.set_setting("multi_pass", True)
    assert link.settings_writes[-1] == ({"twoPass": True}, "rw-settings")
    service.set_setting("pad_wetness", 3)
    assert link.settings_writes[-1] == ({"padWetness": {"reusable": 3}}, "rw-settings")


@pytest.mark.parametrize(
    ("name", "value"),
    [("unknown", True), ("suction", "turbo"), ("pad_wetness", 4), ("child_lock", "yes")],
)
def test_set_setting_rejects_bad_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: Any
) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    service = make_service(Path("/tmp"), loaded_link())
    with pytest.raises(InvalidArgument):
        service.set_setting(name, value)


def test_set_schedule_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link)
    entry = {"start": {"day": 1, "hour": 9, "min": 0}, "cmdStr": "{'command': 'start'}"}
    service.set_schedule([entry])
    assert link.settings_writes[-1] == ({"cleanSchedule2": [entry]}, "rw-schedule")
    with pytest.raises(InvalidArgument):
        service.set_schedule([{"start": {"day": 1}}])
    with pytest.raises(InvalidArgument):
        service.set_schedule([{"start": {"day": 1, "hour": 9, "min": 0}}])


def test_reset_consumable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    rest = FakeRest()
    service = make_service(Path("/tmp"), rest=rest)
    body = service.reset_consumable("47")
    assert body["ok"] is True
    assert rest.reset_calls == ["47"]


def test_raw_publish_is_gated_and_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROOMBA_ALLOW_RAW", raising=False)
    service = make_service(Path("/tmp"), loaded_link())
    with pytest.raises(RawDisabled):
        service.raw_publish("cmd", {"command": "stop"})

    monkeypatch.setenv("ROOMBA_ALLOW_RAW", "1")
    link = loaded_link()
    service = make_service(Path("/tmp"), link)
    service.raw_publish("cmd", {"command": "stop"})
    assert link.raw_writes[-1] == ("cmd", json.dumps({"command": "stop"}))
    with pytest.raises(InvalidArgument):
        service.raw_publish("wifictl", {})


def test_commands_are_logged_as_json(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ROOMBA_ALLOW_WRITES", "1")
    service = make_service(Path("/tmp"), loaded_link())
    with caplog.at_level(logging.INFO, logger="roomba_mcp.commands"):
        service.dock()
    record = caplog.records[-1]
    line = json.loads(record.message)
    assert line["event"] == "command"
    assert line["tool"] == "dock"
    assert line["transport"] == "local"
    assert line["payload"]["command"] == "dock"
