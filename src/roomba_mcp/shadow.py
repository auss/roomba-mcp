"""In-memory Shadow cache.

The robot pushes ``{"state": {"reported": {...}}}`` documents. We merge the
top-level keys into a snapshot and keep the monotonic time of the last write,
so readers can report staleness instead of pretending old data is live.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    reported: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0
    source: str = ""

    def age_s(self, now: float | None = None) -> float:
        if not self.updated_at:
            return float("inf")
        return max(0.0, (now if now is not None else time.monotonic()) - self.updated_at)

    def is_stale(self, ttl_s: float, now: float | None = None) -> bool:
        return self.age_s(now) > ttl_s


def extract_reported(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, dict):
        return None
    reported = state.get("reported")
    return reported if isinstance(reported, dict) else None


def extract_desired(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, dict):
        return None
    desired = state.get("desired")
    return desired if isinstance(desired, dict) else None


class ShadowCache:
    """Thread-safe store for the classic Shadow and the named shadows."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current = Snapshot()
        self._classic = Snapshot()
        self._named: dict[str, Snapshot] = {}
        self._timeline: dict[str, Any] | None = None
        self._timeline_at = 0.0
        self._last_rejection: dict[str, Any] | None = None
        self._last_rejection_at = 0.0

    def update_current(self, reported: dict[str, Any], source: str) -> Snapshot:
        with self._lock:
            merged = dict(self._current.reported)
            merged.update(reported)
            self._current = Snapshot(
                reported=merged,
                updated_at=time.monotonic(),
                source=source,
            )
            return self._current

    def update_classic(self, reported: dict[str, Any], source: str) -> Snapshot:
        """Store the classic shadow separately from live state.

        In the cloud the classic document only carries identity and
        capability fields, so it must not refresh the live-state clock.
        """
        with self._lock:
            merged = dict(self._classic.reported)
            merged.update(reported)
            self._classic = Snapshot(
                reported=merged,
                updated_at=time.monotonic(),
                source=source,
            )
            return self._classic

    def update_named(self, name: str, reported: dict[str, Any], source: str) -> Snapshot:
        with self._lock:
            snapshot = Snapshot(
                reported=dict(reported),
                updated_at=time.monotonic(),
                source=source,
            )
            self._named[name] = snapshot
            return snapshot

    def current(self) -> Snapshot:
        with self._lock:
            return self._current

    def classic(self) -> Snapshot:
        with self._lock:
            return self._classic

    def named(self, name: str) -> Snapshot | None:
        with self._lock:
            return self._named.get(name)

    def set_timeline(self, timeline: dict[str, Any]) -> None:
        with self._lock:
            self._timeline = timeline
            self._timeline_at = time.monotonic()

    def timeline(self) -> tuple[dict[str, Any] | None, float]:
        with self._lock:
            return self._timeline, self._timeline_at

    def set_rejection(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._last_rejection = payload
            self._last_rejection_at = time.monotonic()

    def rejection_since(self, started_at: float) -> dict[str, Any] | None:
        with self._lock:
            if self._last_rejection is None:
                return None
            if self._last_rejection_at < started_at:
                return None
            return self._last_rejection
