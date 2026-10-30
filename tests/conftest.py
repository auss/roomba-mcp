"""Shared test helpers and sanitized fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roomba_mcp.transports.base import WriteResult
from roomba_mcp.transports.manager import LinkSnapshot

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeLink:
    """In-memory stand-in for TransportManager."""

    def __init__(self) -> None:
        self.current: dict[str, Any] = {}
        self.classic: dict[str, Any] = {}
        self.named: dict[str, dict[str, Any]] = {}
        self.commands: list[dict[str, Any]] = []
        self.settings_writes: list[tuple[dict[str, Any], str]] = []
        self.raw_writes: list[tuple[str, str]] = []
        self.outcome = "sent"
        self.transport = "local"
        self.fail_reads: Exception | None = None

    def _snapshot(self, reported: dict[str, Any]) -> LinkSnapshot:
        return LinkSnapshot(
            reported=reported,
            transport=self.transport,
            age_s=0.0,
            stale=False,
        )

    def read_current(self, refresh: bool = False) -> LinkSnapshot:
        if self.fail_reads is not None:
            raise self.fail_reads
        return self._snapshot(self.current)

    def read_classic(self, refresh: bool = False) -> LinkSnapshot:
        if self.fail_reads is not None:
            raise self.fail_reads
        return self._snapshot(self.classic or self.current)

    def read_named(self, name: str, refresh: bool = False) -> LinkSnapshot:
        if self.fail_reads is not None:
            raise self.fail_reads
        return self._snapshot(self.named.get(name, self.current))

    def read_settings(self, refresh: bool = False) -> LinkSnapshot:
        return self.read_named("rw-settings", refresh)

    def read_schedule(self, refresh: bool = False) -> LinkSnapshot:
        return self.read_named("rw-schedule", refresh)

    def write_command(self, payload: dict[str, Any]) -> WriteResult:
        self.commands.append(payload)
        return WriteResult(self.outcome, self.transport)

    def write_settings(self, values: dict[str, Any], shadow: str) -> WriteResult:
        self.settings_writes.append((values, shadow))
        return WriteResult(self.outcome, self.transport)

    def raw_publish(self, topic: str, payload: str) -> WriteResult:
        self.raw_writes.append((topic, payload))
        return WriteResult(self.outcome, self.transport)

    def health(self) -> dict[str, Any]:
        return {"mode": "auto", "active": self.transport, "connected": True}


class FakeRest:
    def __init__(self) -> None:
        self.parts_payload = load_fixture("parts.json")
        self.missions_payload = load_fixture("missionhistory.json")
        self.pmaps_payload = load_fixture("pmaps.json")
        self.reset_calls: list[str] = []

    def mission_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.missions_payload[:limit]

    def parts(self) -> dict[str, Any]:
        return self.parts_payload

    def pmaps(self) -> list[dict[str, Any]]:
        return self.pmaps_payload

    def reset_part(self, part_id: str) -> dict[str, Any]:
        self.reset_calls.append(part_id)
        return {"result": "success"}


@pytest.fixture
def shadows() -> dict[str, Any]:
    return load_fixture("shadows.json")
