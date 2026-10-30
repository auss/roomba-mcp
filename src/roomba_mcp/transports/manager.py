"""Transport selection, failover and write discipline.

Reads may fail over mid-call: a local timeout is retried once over the cloud,
because a read has no side effects. Writes never do: once a write has been
handed to a transport, a failure is reported as ``outcome: "unknown"`` and is
not replayed over the other path.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..auth import LoginCache
from ..config import Config
from ..errors import (
    LoginExpired,
    LoginRequired,
    RoombaError,
    TransportTimeout,
    TransportUnavailable,
    Unsupported,
)
from ..shadow import ShadowCache, Snapshot
from .base import WriteResult
from .cloud import CloudTransport
from .local import LocalTransport

log = logging.getLogger(__name__)

PROBE_CACHE_S = 5.0


@dataclass(frozen=True)
class LinkSnapshot:
    reported: dict[str, Any]
    transport: str
    age_s: float
    stale: bool


class TransportManager:
    def __init__(
        self,
        config: Config,
        login: LoginCache | None,
        cache: ShadowCache,
        *,
        on_change: Callable[[], None] | None = None,
        snapshot_ttl_s: float = 15.0,
    ) -> None:
        self._config = config
        self._login = login
        self._cache = cache
        self._on_change = on_change
        self._snapshot_ttl_s = snapshot_ttl_s
        self._lock = threading.RLock()
        self._active: LocalTransport | CloudTransport | None = None
        self._active_name = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_probe_ok = False
        self._last_probe_at = 0.0
        self._last_error = ""

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="roomba-transport", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)
        with self._lock:
            if self._active is not None:
                self._active.stop()
                self._active = None
                self._active_name = ""

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    self._ensure_locked()
            except RoombaError as exc:
                self._last_error = str(exc)
                log.debug("transport supervisor: %s", exc)
            if self._stop.wait(self._config.transport.probe_interval_s):
                break

    # -- selection ---------------------------------------------------------

    def _probe_local(self) -> bool:
        now = time.monotonic()
        if now - self._last_probe_at < PROBE_CACHE_S:
            return self._last_probe_ok
        robot = self._config.robot
        if not robot.blid:
            self._last_probe_ok = False
            self._last_probe_at = now
            return False
        try:
            with socket.create_connection(
                (robot.resolved_host, robot.port),
                timeout=self._config.transport.probe_timeout_s,
            ):
                ok = True
        except OSError:
            ok = False
        self._last_probe_ok = ok
        self._last_probe_at = now
        return ok

    def _choose(self) -> str:
        mode = self._config.transport.mode
        if mode == "local":
            return "local"
        if mode == "cloud":
            return "cloud"
        return "local" if self._probe_local() else "cloud"

    def _make(self, name: str) -> LocalTransport | CloudTransport:
        if self._login is None:
            raise LoginRequired("no login cache; run 'roomba-mcp login' first")
        if name == "local":
            return LocalTransport(
                blid=self._login.blid or self._config.robot.blid,
                host=self._config.robot.resolved_host,
                port=self._config.robot.port,
                password=self._login.robot_password,
                cache=self._cache,
                on_change=self._notify,
            )
        if self._login.is_expired():
            raise LoginExpired("the cloud session has expired; run 'roomba-mcp login' again")
        return CloudTransport(login=self._login, cache=self._cache, on_change=self._notify)

    def _activate_locked(self, name: str) -> None:
        if self._active is not None:
            self._active.stop()
            self._active = None
            self._active_name = ""
        transport = self._make(name)
        transport.start()
        self._active = transport
        self._active_name = name
        self._last_error = ""

    def _ensure_locked(self) -> LocalTransport | CloudTransport:
        if self._active is not None and self._active.connected:
            return self._active
        if self._active is not None:
            self._active.stop()
            self._active = None
            self._active_name = ""
        choice = self._choose()
        try:
            self._activate_locked(choice)
        except RoombaError as first:
            if self._config.transport.mode != "auto":
                self._last_error = str(first)
                raise
            other = "cloud" if choice == "local" else "local"
            try:
                self._activate_locked(other)
            except RoombaError as second:
                self._last_error = f"{first}; {second}"
                raise TransportUnavailable(
                    "neither transport is available",
                    detail={"local_error": str(first), "cloud_error": str(second)},
                ) from second
        assert self._active is not None
        return self._active

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    # -- reads -------------------------------------------------------------

    def _read_with_failover(
        self, name: str | None, *, current: bool = False
    ) -> tuple[str, dict[str, Any]]:
        transport = self._ensure_locked()
        target = transport.current_shadow if current else name
        try:
            return transport.name, transport.request_shadow(
                target, self._config.transport.read_timeout_s
            )
        except (TransportUnavailable, TransportTimeout) as first:
            if self._config.transport.mode != "auto":
                raise
            other = "cloud" if transport.name == "local" else "local"
            try:
                self._activate_locked(other)
                assert self._active is not None
                target = self._active.current_shadow if current else name
                return self._active.name, self._active.request_shadow(
                    target, self._config.transport.read_timeout_s
                )
            except RoombaError as second:
                raise TransportUnavailable(
                    "both transports failed",
                    detail={"first": str(first), "second": str(second)},
                ) from second

    def read_current(self, refresh: bool = False) -> LinkSnapshot:
        with self._lock:
            snapshot = self._cache.current()
            if (
                snapshot.reported
                and not refresh
                and not snapshot.is_stale(self._snapshot_ttl_s)
            ):
                return LinkSnapshot(
                    reported=snapshot.reported,
                    transport=self._active_name or "cache",
                    age_s=snapshot.age_s(),
                    stale=False,
                )
            try:
                name, reported = self._read_with_failover(None, current=True)
            except (TransportUnavailable, TransportTimeout):
                if snapshot.reported:
                    return LinkSnapshot(
                        reported=snapshot.reported,
                        transport=self._active_name or "cache",
                        age_s=snapshot.age_s(),
                        stale=True,
                    )
                raise
            return LinkSnapshot(reported=reported, transport=name, age_s=0.0, stale=False)

    def _fresh(self, snapshot: Snapshot | None, refresh: bool) -> bool:
        return bool(
            snapshot is not None
            and snapshot.reported
            and not refresh
            and not snapshot.is_stale(self._snapshot_ttl_s)
        )

    def _from_cache(self, snapshot: Snapshot | None) -> LinkSnapshot:
        assert snapshot is not None
        return LinkSnapshot(
            reported=snapshot.reported,
            transport=self._active_name or "cache",
            age_s=snapshot.age_s(),
            stale=False,
        )

    def read_classic(self, refresh: bool = False) -> LinkSnapshot:
        """Read the classic shadow.

        Locally the classic document already holds live state. In the cloud it
        only carries identity and capability fields (``cap``, ``sku``), which
        is exactly what ``get_robot_info`` needs.
        """
        with self._lock:
            if not self._active_name:
                self._ensure_locked()
            if self._active_name == "local":
                snapshot = self._cache.current()
                if self._fresh(snapshot, refresh):
                    return self._from_cache(snapshot)
            else:
                snapshot = self._cache.classic()
                if self._fresh(snapshot, refresh):
                    return self._from_cache(snapshot)
            name, reported = self._read_with_failover(None)
            return LinkSnapshot(reported=reported, transport=name, age_s=0.0, stale=False)

    def read_named(self, name: str, refresh: bool = False) -> LinkSnapshot:
        with self._lock:
            snapshot = self._cache.named(name)
            if self._fresh(snapshot, refresh):
                return self._from_cache(snapshot)
            transport_name, reported = self._read_with_failover(name)
            return LinkSnapshot(
                reported=reported, transport=transport_name, age_s=0.0, stale=False
            )

    def _read_local_state_or_named(self, name: str, refresh: bool) -> LinkSnapshot:
        """Read a setting document from wherever the active transport keeps it.

        The local classic shadow carries settings and schedule directly; the
        cloud keeps them in the ``rw-settings`` and ``rw-schedule`` shadows.
        """
        with self._lock:
            if not self._active_name:
                self._ensure_locked()
            if self._active_name == "local":
                snapshot = self._cache.current()
                if self._fresh(snapshot, refresh):
                    return self._from_cache(snapshot)
                transport_name, reported = self._read_with_failover(None)
                return LinkSnapshot(
                    reported=reported, transport=transport_name, age_s=0.0, stale=False
                )
            named = self._cache.named(name)
            if self._fresh(named, refresh):
                return self._from_cache(named)
            transport_name, reported = self._read_with_failover(name)
            return LinkSnapshot(
                reported=reported, transport=transport_name, age_s=0.0, stale=False
            )

    def read_settings(self, refresh: bool = False) -> LinkSnapshot:
        return self._read_local_state_or_named("rw-settings", refresh)

    def read_schedule(self, refresh: bool = False) -> LinkSnapshot:
        return self._read_local_state_or_named("rw-schedule", refresh)

    # -- writes ------------------------------------------------------------

    def write_command(self, payload: dict[str, Any]) -> WriteResult:
        with self._lock:
            transport = self._ensure_locked()
            try:
                return transport.write_command(payload, self._config.transport.read_timeout_s)
            except (TransportUnavailable, TransportTimeout) as exc:
                return WriteResult("unknown", transport.name, str(exc))

    def write_settings(self, values: dict[str, Any], shadow: str) -> WriteResult:
        with self._lock:
            transport = self._ensure_locked()
            try:
                return transport.write_settings(
                    values, shadow, self._config.transport.read_timeout_s
                )
            except (TransportUnavailable, TransportTimeout) as exc:
                return WriteResult("unknown", transport.name, str(exc))

    def raw_publish(self, topic: str, payload: str) -> WriteResult:
        with self._lock:
            transport = self._ensure_locked()
            if transport.name != "local":
                raise Unsupported("raw_publish is available on the local transport only")
            return transport.raw_publish(topic, payload)

    # -- status ------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        with self._lock:
            login = self._login
            return {
                "mode": self._config.transport.mode,
                "active": self._active_name,
                "connected": bool(self._active is not None and self._active.connected),
                "local_probe_ok": self._last_probe_ok,
                "local_probe_age_s": (
                    round(time.monotonic() - self._last_probe_at, 1)
                    if self._last_probe_at
                    else None
                ),
                "login_present": login is not None,
                "login_expired": login.is_expired() if login is not None else None,
                "last_error": self._last_error,
            }
