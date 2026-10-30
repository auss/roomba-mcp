"""Transport contracts shared by the local and cloud links."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class WriteResult:
    outcome: str
    transport: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"outcome": self.outcome, "transport": self.transport}
        if self.detail:
            body["detail"] = self.detail
        return body


class Transport(Protocol):
    name: str
    # Named shadow that carries live state. ``None`` means the classic shadow.
    current_shadow: str | None

    def start(self) -> None: ...

    def stop(self) -> None: ...

    @property
    def connected(self) -> bool: ...

    def request_shadow(self, name: str | None, timeout: float) -> dict[str, Any]: ...

    def write_command(self, payload: dict[str, Any], timeout: float) -> WriteResult: ...

    def write_settings(
        self, values: dict[str, Any], shadow: str, timeout: float
    ) -> WriteResult: ...

    def raw_publish(self, topic: str, payload: str) -> WriteResult: ...
