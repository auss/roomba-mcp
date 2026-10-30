"""Capability tiers and the safety gates around them.

Tiers describe confidence, not danger:

* ``verified`` - observed working against this robot and firmware.
* ``derived-low-risk`` - reverse-engineered from the app, reversible, and
  allowed by default (writes still need ``ROOMBA_ALLOW_WRITES=1``).
* ``unverified-physical`` - the wire format is unproven; a wrong payload can
  move the robot. Hard error unless ``ROOMBA_EXPERIMENTAL=1``.

Reads never hard-error on tier. ``get_robot_info`` publishes this table so a
bot can decide before it acts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import ExperimentalRequired, RawDisabled, WritesDisabled

VERIFIED = "verified"
DERIVED = "derived-low-risk"
UNVERIFIED = "unverified-physical"


@dataclass(frozen=True)
class Capability:
    tier: str
    transports: dict[str, str]
    note: str = ""


def _cap(tier: str, local: str, cloud: str, note: str = "") -> Capability:
    return Capability(tier=tier, transports={"local": local, "cloud": cloud}, note=note)


CAPABILITIES: dict[str, Capability] = {
    "status": _cap(VERIFIED, VERIFIED, VERIFIED),
    "robot_info": _cap(VERIFIED, VERIFIED, VERIFIED),
    "settings_read": _cap(VERIFIED, VERIFIED, VERIFIED),
    "schedule_read": _cap(VERIFIED, VERIFIED, VERIFIED),
    "mission_current": _cap(VERIFIED, VERIFIED, VERIFIED),
    "mission_history": _cap(DERIVED, "unsupported", DERIVED, "cloud REST only"),
    "consumables_read": _cap(DERIVED, "unsupported", DERIVED, "cloud REST only"),
    "map_read": _cap(DERIVED, "unsupported", DERIVED, "cloud REST only"),
    "find": _cap(VERIFIED, VERIFIED, DERIVED),
    "start_clean": _cap(VERIFIED, VERIFIED, DERIVED),
    "start_clean_parameterized": _cap(
        UNVERIFIED,
        UNVERIFIED,
        UNVERIFIED,
        "regions, passes, suction or edge_clean; needs ROOMBA_EXPERIMENTAL=1",
    ),
    "pause_clean": _cap(VERIFIED, VERIFIED, DERIVED),
    "resume_clean": _cap(VERIFIED, VERIFIED, DERIVED),
    "stop_clean": _cap(VERIFIED, VERIFIED, DERIVED),
    "dock": _cap(VERIFIED, VERIFIED, DERIVED),
    "set_setting": _cap(DERIVED, DERIVED, DERIVED, "typed subset only"),
    "set_schedule": _cap(DERIVED, DERIVED, DERIVED),
    "reset_consumable": _cap(DERIVED, "unsupported", DERIVED, "cloud REST only"),
    "raw_publish": _cap(UNVERIFIED, UNVERIFIED, "unsupported", "debug only, local only"),
}


def capability_report() -> dict[str, dict[str, object]]:
    return {
        name: {"tier": cap.tier, "transports": dict(cap.transports), "note": cap.note}
        for name, cap in CAPABILITIES.items()
    }


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def writes_enabled() -> bool:
    return _enabled("ROOMBA_ALLOW_WRITES")


def raw_enabled() -> bool:
    return _enabled("ROOMBA_ALLOW_RAW")


def experimental_enabled() -> bool:
    return _enabled("ROOMBA_EXPERIMENTAL")


def require_writes() -> None:
    if not writes_enabled():
        raise WritesDisabled(
            "writes are disabled; set ROOMBA_ALLOW_WRITES=1 to enable commands",
        )


def require_raw() -> None:
    if not raw_enabled():
        raise RawDisabled("raw_publish is disabled; set ROOMBA_ALLOW_RAW=1 to enable it")


def require_experimental(what: str) -> None:
    if not experimental_enabled():
        raise ExperimentalRequired(
            f"{what} is unverified on this firmware; set ROOMBA_EXPERIMENTAL=1 to allow it",
        )
