"""Wire payloads for the robot's MQTT interface.

The command envelope is the one the app and ``roomba-control.py`` use:

    {"command": "start", "time": <epoch>, "initiator": "localApp"}

Settings travel as ``{"state": {"<key>": <value>}}`` on the ``delta`` topic
locally, or as a named-shadow desired document in the cloud. The initiator
stays ``localApp`` because that is the value the robot accepts.
"""

from __future__ import annotations

import time
from typing import Any

from .errors import InvalidArgument

INITIATOR = "localApp"
SUCTION_LEVELS = ("normal", "high")


def command(name: str, *, now: float | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "command": name,
        "time": int(now if now is not None else time.time()),
        "initiator": INITIATOR,
    }
    body.update(extra)
    return body


def setting_state(values: dict[str, Any]) -> dict[str, Any]:
    return {"state": values}


def desired_state(values: dict[str, Any]) -> dict[str, Any]:
    return {"state": {"desired": values}}


def validate_passes(passes: int | None) -> int | None:
    if passes is None:
        return None
    if passes not in (1, 2):
        raise InvalidArgument("passes must be 1 or 2", detail={"value": passes})
    return passes


def validate_suction(suction: str | None) -> str | None:
    if suction is None:
        return None
    if suction not in SUCTION_LEVELS:
        raise InvalidArgument(
            f"suction must be one of {', '.join(SUCTION_LEVELS)}",
            detail={"value": suction},
        )
    return suction


def region_clean(
    *,
    pmap_id: str,
    regions: list[str],
    passes: int | None = None,
    suction: str | None = None,
    edge_clean: bool | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Build the parameterized start command used by schedules.

    This is the unverified-physical path: the schedule shadow stores the same
    shape as ``cmdStr``, but an immediate region clean has not been proven on
    this firmware. Callers must hold ``ROOMBA_EXPERIMENTAL=1``.
    """
    if not regions:
        raise InvalidArgument("regions must contain at least one region id")
    params: dict[str, Any] = {}
    if passes is not None:
        params["twoPass"] = passes == 2
        params["noAutoPasses"] = passes == 1
    if suction is not None:
        params["vacHigh"] = suction == "high"
    if edge_clean is not None:
        params["edgeClean"] = edge_clean
    return command(
        "start",
        now=now,
        ordered=1,
        pmap_id=pmap_id,
        regions=[
            {"type": "rid", "region_id": str(region), "params": dict(params)}
            for region in regions
        ],
    )
