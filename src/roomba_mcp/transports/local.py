"""Local LSS transport: MQTT over TLS to the robot on port 8883.

The TLS recipe is the one proven in the source project: accept the
iRobot-signed certificate, allow the legacy ``AES128-SHA256`` CBC cipher with
``@SECLEVEL=0``, and set ``SSL_OP_LEGACY_SERVER_CONNECT``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from ..errors import TransportTimeout, TransportUnavailable
from ..shadow import ShadowCache, extract_reported
from .base import WriteResult

log = logging.getLogger(__name__)

DEFAULT_CIPHERS = "AES128-SHA256"
REJECTION_WINDOW_S = 1.5


def build_ssl_context(ciphers: str = DEFAULT_CIPHERS) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    for candidate in (f"{ciphers}:@SECLEVEL=0", ciphers, "DEFAULT:@SECLEVEL=0"):
        try:
            context.set_ciphers(candidate)
            break
        except ssl.SSLError:
            continue
    with contextlib.suppress(AttributeError, ValueError):
        context.options |= 0x4  # SSL_OP_LEGACY_SERVER_CONNECT
    return context


class LocalTransport:
    name = "local"
    current_shadow: str | None = None

    def __init__(
        self,
        *,
        blid: str,
        host: str,
        port: int,
        password: str,
        cache: ShadowCache,
        on_change: Callable[[], None] | None = None,
        ciphers: str = DEFAULT_CIPHERS,
    ) -> None:
        self._blid = blid
        self._host = host
        self._port = port
        self._password = password
        self._cache = cache
        self._on_change = on_change
        self._ciphers = ciphers
        self._client: mqtt.Client | None = None
        self._connected = False
        self._started = False
        self._shadow_event = threading.Event()
        self._waiting_for: str | None = None
        self._last_error = ""

    @property
    def connected(self) -> bool:
        client = self._client
        return bool(self._connected and client is not None and client.is_connected())

    @property
    def last_error(self) -> str:
        return self._last_error

    def start(self) -> None:
        if not self._blid or not self._password:
            raise TransportUnavailable(
                "local transport needs a robot id and password; run 'roomba-mcp login'"
            )
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=self._blid,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self._blid, self._password)
        client.tls_set_context(build_ssl_context(self._ciphers))
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client
        try:
            client.connect_async(self._host, self._port, keepalive=60)
            client.loop_start()
        except Exception as exc:  # paho raises a variety of socket/ssl errors
            self._client = None
            self._last_error = str(exc)
            raise TransportUnavailable(f"cannot start local MQTT: {exc}") from exc
        self._started = True

    def stop(self) -> None:
        self._started = False
        self._connected = False
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(Exception):
            client.disconnect()
        with contextlib.suppress(Exception):
            client.loop_stop()

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        if reason_code.is_failure:
            self._connected = False
            self._last_error = f"MQTT connect refused: {reason_code.getName()}"
            log.warning(self._last_error)
            return
        self._connected = True
        self._last_error = ""
        client.subscribe("#", qos=1)

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any = None,
        reason_code: Any = None,
        properties: Any = None,
    ) -> None:
        self._connected = False

    def _on_message(self, client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        topic = message.topic
        if "rejected" in topic:
            if isinstance(payload, dict):
                self._cache.set_rejection(payload)
            self._notify()
            return
        if topic == "timeline" or "mission/timeline" in topic:
            if isinstance(payload, dict):
                self._cache.set_timeline(payload)
            self._notify()
            return
        reported = extract_reported(payload)
        if reported is None:
            return
        named = None
        if isinstance(payload, dict):
            named = payload.get("namedShadow") or payload.get("shadowName")
        if isinstance(named, str) and named:
            self._cache.update_named(named, reported, "local")
        else:
            self._cache.update_current(reported, "local")
        self._shadow_event.set()
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:  # never let a listener kill the MQTT thread
                log.exception("shadow change listener failed")

    def _publish(self, topic: str, payload: dict[str, Any], timeout: float) -> bool:
        client = self._client
        if client is None or not self.connected:
            raise TransportUnavailable("local transport is not connected")
        info = client.publish(topic, json.dumps(payload), qos=1)
        try:
            info.wait_for_publish(timeout)
        except (RuntimeError, ValueError) as exc:
            raise TransportUnavailable(f"local publish failed: {exc}") from exc
        return bool(info.is_published())

    def request_shadow(self, name: str | None, timeout: float) -> dict[str, Any]:
        self._shadow_event.clear()
        body: dict[str, Any] = {"command": "get"}
        if name:
            body["namedShadow"] = name
        if not self._publish("shadow", body, timeout=5.0):
            raise TransportUnavailable("the robot did not accept the shadow request")
        self._shadow_event.wait(timeout)
        if name:
            snapshot = self._cache.named(name)
            if snapshot is not None and snapshot.reported:
                return snapshot.reported
        snapshot = self._cache.current()
        if snapshot.reported:
            return snapshot.reported
        raise TransportTimeout(
            "the robot did not answer the shadow request",
            detail={"transport": "local", "timeout_s": timeout},
        )

    def _wait_rejection(self, started_at: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + REJECTION_WINDOW_S
        while time.monotonic() < deadline:
            rejection = self._cache.rejection_since(started_at)
            if rejection is not None:
                return rejection
            time.sleep(0.05)
        return None

    def write_command(self, payload: dict[str, Any], timeout: float) -> WriteResult:
        started_at = time.monotonic()
        if not self._publish("cmd", payload, timeout):
            return WriteResult("unknown", self.name, "publish was not confirmed")
        rejection = self._wait_rejection(started_at)
        if rejection is not None:
            return WriteResult("rejected", self.name, json.dumps(rejection)[:500])
        return WriteResult("sent", self.name)

    def write_settings(
        self, values: dict[str, Any], shadow: str, timeout: float
    ) -> WriteResult:
        started_at = time.monotonic()
        if not self._publish("delta", {"state": values}, timeout):
            return WriteResult("unknown", self.name, "publish was not confirmed")
        rejection = self._wait_rejection(started_at)
        if rejection is not None:
            return WriteResult("rejected", self.name, json.dumps(rejection)[:500])
        return WriteResult("sent", self.name)

    def raw_publish(self, topic: str, payload: str) -> WriteResult:
        client = self._client
        if client is None or not self.connected:
            raise TransportUnavailable("local transport is not connected")
        info = client.publish(topic, payload, qos=1)
        try:
            info.wait_for_publish(5.0)
        except (RuntimeError, ValueError) as exc:
            return WriteResult("unknown", self.name, str(exc))
        return WriteResult("sent", self.name)
