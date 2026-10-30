"""Typed errors that cross the tool boundary.

Every expected failure carries a stable ``code`` so a bot can branch on it
without parsing prose. Unexpected exceptions are left to the MCP layer.
"""

from __future__ import annotations

from typing import Any


class RoombaError(Exception):
    """Base class for expected, user-facing failures."""

    code = "roomba_error"

    def __init__(self, message: str, *, detail: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


class ConfigError(RoombaError):
    code = "config_error"


class LoginRequired(RoombaError):
    code = "login_required"


class LoginExpired(RoombaError):
    code = "login_expired"


class LoginFailed(RoombaError):
    code = "login_failed"


class TransportUnavailable(RoombaError):
    code = "transport_unavailable"


class TransportTimeout(RoombaError):
    code = "transport_timeout"


class WritesDisabled(RoombaError):
    code = "writes_disabled"


class RawDisabled(RoombaError):
    code = "raw_disabled"


class ExperimentalRequired(RoombaError):
    code = "experimental_required"


class InvalidArgument(RoombaError):
    code = "invalid_argument"


class Unsupported(RoombaError):
    code = "unsupported"


class CloudHTTPError(RoombaError):
    code = "cloud_http_error"

    def __init__(self, message: str, *, status: int, body: str = "") -> None:
        super().__init__(message, detail={"status": status, "body": body[:2000]})
        self.status = status
