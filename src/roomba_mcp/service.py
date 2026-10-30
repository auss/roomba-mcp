"""Intent-level robot operations.

Tools call these methods; the methods own validation, capability gates,
payload construction and command logging. The transport layer stays thin.
"""

from __future__ import annotations

import datetime
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .capabilities import (
    capability_report,
    experimental_enabled,
    raw_enabled,
    require_experimental,
    require_raw,
    require_writes,
    writes_enabled,
)
from .config import Config
from .errors import InvalidArgument, LoginRequired, RoombaError
from .logging import log_command
from .payloads import (
    command,
    region_clean,
    validate_passes,
    validate_suction,
)
from .shadow import ShadowCache
from .transports.base import WriteResult
from .transports.manager import LinkSnapshot

SETTINGS: dict[str, str] = {
    "suction": "suction",
    "carpet_boost": "bool",
    "edge_clean": "bool",
    "pad_wetness": "pad_wetness",
    "multi_pass": "bool",
    "bin_pause": "bool",
    "child_lock": "bool",
}
SETTING_KEYS = {
    "suction": "vacHigh",
    "carpet_boost": "carpetBoost",
    "edge_clean": "edgeClean",
    "multi_pass": "twoPass",
    "bin_pause": "binPause",
    "child_lock": "childLock",
}
RAW_TOPICS = ("cmd", "delta", "shadow", "timeline", "testcmd")
SNAPSHOT_TTL_S = 15.0
MAP_TTL_S = 60.0


class Link(Protocol):
    def read_current(self, refresh: bool = False) -> LinkSnapshot: ...

    def read_classic(self, refresh: bool = False) -> LinkSnapshot: ...

    def read_named(self, name: str, refresh: bool = False) -> LinkSnapshot: ...

    def read_settings(self, refresh: bool = False) -> LinkSnapshot: ...

    def read_schedule(self, refresh: bool = False) -> LinkSnapshot: ...

    def write_command(self, payload: dict[str, Any]) -> WriteResult: ...

    def write_settings(self, values: dict[str, Any], shadow: str) -> WriteResult: ...

    def raw_publish(self, topic: str, payload: str) -> WriteResult: ...

    def health(self) -> dict[str, Any]: ...


@dataclass
class RobotService:
    config: Config
    link: Link
    rest: Any | None = None
    cache: ShadowCache | None = None
    now: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        if self.cache is None:
            self.cache = ShadowCache()
        self._maps_cache: list[dict[str, Any]] | None = None
        self._maps_cache_at = 0.0

    # -- helpers -----------------------------------------------------------

    def _clock(self) -> float:
        return time.monotonic()

    def _rest_client(self) -> Any:
        if self.rest is None:
            raise LoginRequired("cloud reads need a login cache; run 'roomba-mcp login'")
        return self.rest

    def _iso(self, timestamp: Any) -> str | None:
        if not isinstance(timestamp, (int, float)):
            return None
        try:
            return datetime.datetime.fromtimestamp(timestamp, datetime.UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    def _status_payload(
        self,
        reported: dict[str, Any],
        *,
        transport: str,
        stale: bool,
        age_s: float,
    ) -> dict[str, Any]:
        mission = reported.get("cleanMissionStatus")
        mission = mission if isinstance(mission, dict) else {}
        bin_state = reported.get("bin")
        bin_state = bin_state if isinstance(bin_state, dict) else {}
        return {
            "ok": True,
            "transport": transport,
            "stale": stale,
            "age_s": round(age_s, 1),
            "name": reported.get("name"),
            "sku": reported.get("sku"),
            "software_version": reported.get("softwareVer"),
            "battery_pct": reported.get("batPct"),
            "phase": mission.get("phase"),
            "cycle": mission.get("cycle"),
            "mission": {
                "phase": mission.get("phase"),
                "cycle": mission.get("cycle"),
                "mission_id": mission.get("missionId"),
                "number": mission.get("nMssn"),
                "error": mission.get("error"),
                "not_ready": mission.get("notReady"),
                "runtime_min": mission.get("mssnM"),
                "area_sqft": mission.get("sqft"),
            },
            "bin": {
                "full": bin_state.get("full"),
                "present": bin_state.get("present"),
            },
            "tank": {
                "level": reported.get("tankLvl"),
                "present": reported.get("tankPresent"),
            },
            "pad": reported.get("detectedPad"),
            "position": reported.get("pose"),
            "wifi": reported.get("signal"),
            "reported": reported,
        }

    def _settings_payload(
        self, reported: dict[str, Any], *, transport: str, stale: bool, age_s: float
    ) -> dict[str, Any]:
        pad = reported.get("padWetness")
        reusable = pad.get("reusable") if isinstance(pad, dict) else None
        values = {
            "suction": (
                "high"
                if reported.get("vacHigh") is True
                else "normal"
                if "vacHigh" in reported
                else None
            ),
            "carpet_boost": reported.get("carpetBoost"),
            "edge_clean": reported.get("edgeClean"),
            "pad_wetness": reusable,
            "multi_pass": reported.get("twoPass"),
            "bin_pause": reported.get("binPause"),
            "child_lock": reported.get("childLock"),
        }
        return {
            "ok": True,
            "transport": transport,
            "stale": stale,
            "age_s": round(age_s, 1),
            "values": values,
        }

    def _schedule_payload(
        self, reported: dict[str, Any], *, transport: str, stale: bool, age_s: float
    ) -> dict[str, Any]:
        schedule = reported.get("cleanSchedule2")
        if schedule is None:
            schedule = reported.get("cleanSchedule") or []
        return {
            "ok": True,
            "transport": transport,
            "stale": stale,
            "age_s": round(age_s, 1),
            "schedule": schedule,
        }

    def _mission_payload(self, reported: dict[str, Any]) -> dict[str, Any]:
        mission = reported.get("cleanMissionStatus")
        mission = mission if isinstance(mission, dict) else {}
        timeline, timeline_at = self.cache.timeline() if self.cache else (None, 0.0)
        return {
            "ok": True,
            "phase": mission.get("phase"),
            "cycle": mission.get("cycle"),
            "mission_id": mission.get("missionId"),
            "number": mission.get("nMssn"),
            "error": mission.get("error"),
            "runtime_min": mission.get("mssnM"),
            "area_sqft": mission.get("sqft"),
            "battery_pct": reported.get("batPct"),
            "timeline": timeline,
            "timeline_age_s": (
                round(self._clock() - timeline_at, 1) if timeline_at else None
            ),
        }

    # -- reads -------------------------------------------------------------

    def get_status(self, refresh: bool = False) -> dict[str, Any]:
        snapshot = self.link.read_current(refresh=refresh)
        return self._status_payload(
            snapshot.reported,
            transport=snapshot.transport,
            stale=snapshot.stale,
            age_s=snapshot.age_s,
        )

    def get_mission(self, refresh: bool = False) -> dict[str, Any]:
        snapshot = self.link.read_current(refresh=refresh)
        return self._mission_payload(snapshot.reported)

    def get_robot_info(self) -> dict[str, Any]:
        robot: dict[str, Any] = {"blid": self.config.robot.blid or None}
        errors: list[str] = []
        try:
            classic = self.link.read_classic()
            for key in ("sku", "cap", "digiCap", "softwareVer"):
                if classic.reported.get(key) is not None:
                    robot[key] = classic.reported[key]
        except RoombaError as exc:
            errors.append(f"classic shadow: {exc}")
        try:
            current = self.link.read_current()
            for key in ("name", "softwareVer", "sku"):
                if current.reported.get(key) is not None:
                    robot[key] = current.reported[key]
        except RoombaError as exc:
            errors.append(f"current shadow: {exc}")
        if not robot.get("name"):
            # In the cloud the robot name lives in rw-settings, not in the
            # classic or live shadow.
            try:
                settings = self.link.read_settings()
                for key in ("name", "softwareVer", "sku"):
                    if settings.reported.get(key) is not None:
                        robot[key] = settings.reported[key]
            except RoombaError as exc:
                errors.append(f"settings shadow: {exc}")
        if not robot.get("name"):
            robot["name"] = None
        return {
            "ok": True,
            "robot": robot,
            "transport": self.link.health(),
            "capabilities": capability_report(),
            "safety": {
                "writes_enabled": writes_enabled(),
                "raw_enabled": raw_enabled(),
                "experimental_enabled": experimental_enabled(),
            },
            "config": {
                "transport_mode": self.config.transport.mode,
                "robot_host": self.config.robot.resolved_host,
                "robot_port": self.config.robot.port,
                "http": {
                    "host": self.config.http.host,
                    "port": self.config.http.port,
                },
            },
            "errors": errors,
        }

    def get_settings(self, refresh: bool = False) -> dict[str, Any]:
        snapshot = self.link.read_settings(refresh=refresh)
        return self._settings_payload(
            snapshot.reported,
            transport=snapshot.transport,
            stale=snapshot.stale,
            age_s=snapshot.age_s,
        )

    def get_schedule(self, refresh: bool = False) -> dict[str, Any]:
        snapshot = self.link.read_schedule(refresh=refresh)
        return self._schedule_payload(
            snapshot.reported,
            transport=snapshot.transport,
            stale=snapshot.stale,
            age_s=snapshot.age_s,
        )

    def get_mission_history(self, limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise InvalidArgument("limit must be between 1 and 100")
        missions = self._rest_client().mission_history(limit)
        summaries = [self._mission_summary(item) for item in missions]
        return {"ok": True, "count": len(summaries), "missions": summaries}

    def _mission_summary(self, mission: dict[str, Any]) -> dict[str, Any]:
        timeline = mission.get("timeline")
        timeline = timeline if isinstance(timeline, dict) else {}
        events = timeline.get("event")
        events = events if isinstance(events, list) else []
        done = events[-1].get("type") if events and isinstance(events[-1], dict) else None
        error = None
        for event in timeline.get("finEvents") or []:
            if isinstance(event, dict) and event.get("type") == "error":
                error = event.get("error")
                break
        maps = mission.get("pmaps_info")
        map_ids = [
            item.get("pmap_id")
            for item in (maps if isinstance(maps, list) else [])
            if isinstance(item, dict) and item.get("pmap_id")
        ]
        return {
            "mission_id": mission.get("missionId") or timeline.get("mission_id"),
            "started_at": self._iso(mission.get("timestamp")),
            "duration_min": mission.get("runM"),
            "area_sqft": mission.get("sqft"),
            "initiator": mission.get("initiator"),
            "done_code": done,
            "error": error,
            "map_ids": map_ids,
            "software_version": mission.get("softwareVer"),
        }

    def get_consumables(self) -> dict[str, Any]:
        payload = self._rest_client().parts()
        parts = payload.get("parts") if isinstance(payload, dict) else None
        parts = parts if isinstance(parts, list) else []
        summaries = []
        for part in parts:
            if not isinstance(part, dict):
                continue
            counter = part.get("counter")
            summaries.append(
                {
                    "part_id": part.get("part_id"),
                    "counter_pct": counter,
                    "count_type": part.get("count_type"),
                    "count_used": part.get("count_used"),
                    "count_remaining": part.get("count_remaining"),
                    "minutes_remaining": part.get("minutes_remaining"),
                    "reset_by": part.get("reset_by"),
                    "last_updated": self._iso(part.get("last_updated_ts")),
                    "needs_attention": isinstance(counter, (int, float)) and counter <= 10,
                }
            )
        return {
            "ok": True,
            "num_parts": payload.get("num_parts", len(summaries)),
            "parts": summaries,
        }

    def _maps(self, refresh: bool = False) -> list[dict[str, Any]]:
        now = self._clock()
        if (
            not refresh
            and self._maps_cache is not None
            and now - self._maps_cache_at < MAP_TTL_S
        ):
            return self._maps_cache
        raw = self._rest_client().pmaps()
        maps: list[dict[str, Any]] = []
        for item in raw:
            details = item.get("active_pmapv_details")
            details = details if isinstance(details, dict) else {}
            header = details.get("map_header")
            header = header if isinstance(header, dict) else {}
            regions = details.get("regions")
            regions = regions if isinstance(regions, list) else []
            maps.append(
                {
                    "pmap_id": item.get("pmap_id"),
                    "name": header.get("name"),
                    "active_pmapv_id": item.get("active_pmapv_id"),
                    "area_m2": header.get("area"),
                    "regions": [
                        {
                            "id": str(region.get("id")),
                            "name": region.get("name"),
                            "type": region.get("region_type"),
                        }
                        for region in regions
                        if isinstance(region, dict) and region.get("id") is not None
                    ],
                }
            )
        self._maps_cache = maps
        self._maps_cache_at = now
        return maps

    def get_map(self, refresh: bool = False) -> dict[str, Any]:
        maps = self._maps(refresh=refresh)
        return {"ok": True, "count": len(maps), "maps": maps}

    # -- writes ------------------------------------------------------------

    def _write_result(
        self, tool: str, payload: Any, result: WriteResult
    ) -> dict[str, Any]:
        body = result.as_dict()
        body["ok"] = result.outcome != "rejected"
        body["payload"] = payload
        log_command(tool, payload, transport=result.transport, result=body)
        return body

    def _simple_command(self, tool: str, name: str) -> dict[str, Any]:
        require_writes()
        payload = command(name, now=self.now())
        return self._write_result(tool, payload, self.link.write_command(payload))

    def find_robot(self) -> dict[str, Any]:
        return self._simple_command("find_robot", "find")

    def pause_clean(self) -> dict[str, Any]:
        return self._simple_command("pause_clean", "pause")

    def resume_clean(self) -> dict[str, Any]:
        return self._simple_command("resume_clean", "resume")

    def stop_clean(self) -> dict[str, Any]:
        return self._simple_command("stop_clean", "stop")

    def dock(self) -> dict[str, Any]:
        return self._simple_command("dock", "dock")

    def start_clean(
        self,
        regions: list[str] | None = None,
        passes: int | None = None,
        suction: str | None = None,
        edge_clean: bool | None = None,
    ) -> dict[str, Any]:
        require_writes()
        parameterized = any(
            value is not None for value in (regions, passes, suction, edge_clean)
        )
        if parameterized:
            require_experimental("parameterized start_clean")
            if not regions:
                raise InvalidArgument(
                    "regions are required when passes, suction or edge_clean are set"
                )
            passes = validate_passes(passes)
            suction = validate_suction(suction)
            if edge_clean is not None and not isinstance(edge_clean, bool):
                raise InvalidArgument("edge_clean must be true or false")
            maps = self._maps()
            requested = {str(region) for region in regions}
            matches = [
                item
                for item in maps
                if requested <= {region["id"] for region in item["regions"]}
            ]
            if not matches:
                raise InvalidArgument(
                    "no smart map contains all requested region ids",
                    detail={
                        "regions": sorted(requested),
                        "maps": [
                            {"pmap_id": item["pmap_id"], "regions": item["regions"]}
                            for item in maps
                        ],
                    },
                )
            if len(matches) > 1:
                raise InvalidArgument(
                    "the requested regions span several smart maps",
                    detail={"pmap_ids": [item["pmap_id"] for item in matches]},
                )
            payload = region_clean(
                pmap_id=str(matches[0]["pmap_id"]),
                regions=[str(region) for region in regions],
                passes=passes,
                suction=suction,
                edge_clean=edge_clean,
                now=self.now(),
            )
        else:
            payload = command("start", now=self.now())
        return self._write_result("start_clean", payload, self.link.write_command(payload))

    def set_setting(self, name: str, value: Any) -> dict[str, Any]:
        require_writes()
        kind = SETTINGS.get(name)
        if kind is None:
            raise InvalidArgument(
                "unknown setting",
                detail={"allowed": sorted(SETTINGS)},
            )
        state: dict[str, Any]
        if kind == "bool":
            if not isinstance(value, bool):
                raise InvalidArgument(f"{name} must be true or false")
            state = {SETTING_KEYS[name]: value}
        elif kind == "suction":
            if value not in ("normal", "high"):
                raise InvalidArgument("suction must be normal or high")
            state = {"vacHigh": value == "high"}
        else:  # pad_wetness
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3:
                raise InvalidArgument("pad_wetness must be 1, 2 or 3")
            state = {"padWetness": {"reusable": value}}
        result = self.link.write_settings(state, "rw-settings")
        return self._write_result("set_setting", state, result)

    def set_schedule(self, schedule: list[dict[str, Any]]) -> dict[str, Any]:
        require_writes()
        if not isinstance(schedule, list):
            raise InvalidArgument("schedule must be a list")
        for index, entry in enumerate(schedule):
            if not isinstance(entry, dict):
                raise InvalidArgument(f"schedule[{index}] must be an object")
            start = entry.get("start")
            if not isinstance(start, dict) or not all(
                key in start for key in ("day", "hour", "min")
            ):
                raise InvalidArgument(f"schedule[{index}].start needs day, hour and min")
            if not isinstance(entry.get("cmdStr"), str):
                raise InvalidArgument(f"schedule[{index}].cmdStr must be a string")
        payload = {"cleanSchedule2": schedule}
        result = self.link.write_settings(payload, "rw-schedule")
        return self._write_result("set_schedule", payload, result)

    def reset_consumable(self, part: str) -> dict[str, Any]:
        require_writes()
        result = self._rest_client().reset_part(part)
        body = {"ok": True, "part": part, "result": result}
        log_command("reset_consumable", {"part": part}, transport="cloud", result=body)
        return body

    def raw_publish(self, topic: str, payload: Any) -> dict[str, Any]:
        require_raw()
        if topic not in RAW_TOPICS:
            raise InvalidArgument(
                "topic is not allowed",
                detail={"allowed": list(RAW_TOPICS)},
            )
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload)
        elif isinstance(payload, str):
            body = payload
        else:
            raise InvalidArgument("payload must be an object, array or string")
        result = self.link.raw_publish(topic, body)
        return self._write_result("raw_publish", {"topic": topic, "body": body}, result)

    # -- resources ---------------------------------------------------------

    def resource_payloads(self) -> dict[str, dict[str, Any]]:
        assert self.cache is not None
        current = self.cache.current()
        settings = self.cache.named("rw-settings") or current
        schedule = self.cache.named("rw-schedule") or current
        age = current.age_s()
        stale = current.is_stale(SNAPSHOT_TTL_S)
        return {
            "roomba://status": self._status_payload(
                current.reported,
                transport=self.link.health().get("active") or "cache",
                stale=stale,
                age_s=age,
            ),
            "roomba://mission": self._mission_payload(current.reported),
            "roomba://settings": self._settings_payload(
                settings.reported,
                transport=self.link.health().get("active") or "cache",
                stale=stale,
                age_s=age,
            ),
            "roomba://schedule": self._schedule_payload(
                schedule.reported,
                transport=self.link.health().get("active") or "cache",
                stale=stale,
                age_s=age,
            ),
        }
