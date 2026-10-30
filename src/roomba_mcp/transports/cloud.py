"""Cloud transport: AWS IoT MQTT over WebSocket as the iRobot app.

One connection carries one SUBSCRIBE packet with every filter we need. The
authorizer rejects a second SUBSCRIBE on the same connection, but a single
multi-filter SUBSCRIBE is accepted (verified against the live broker).
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

from ..auth import LoginCache
from ..errors import TransportTimeout, TransportUnavailable, Unsupported
from ..shadow import ShadowCache, extract_reported
from .base import WriteResult

log = logging.getLogger(__name__)

REJECTION_WINDOW_S = 1.5
ACK_TIMEOUT_S = 5.0


class CloudTransport:
    name = "cloud"
    current_shadow = "ro-currentstate"

    def __init__(
        self,
        *,
        login: LoginCache,
        cache: ShadowCache,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._login = login
        self._cache = cache
        self._on_change = on_change
        self._client: mqtt.Client | None = None
        self._connected = False
        self._started = False
        self._responses: dict[str, dict[str, Any]] = {}
        self._event = threading.Event()
        self._last_error = ""

    @property
    def connected(self) -> bool:
        client = self._client
        return bool(self._connected and client is not None and client.is_connected())

    @property
    def last_error(self) -> str:
        return self._last_error

    def _topic(self, suffix: str) -> str:
        return f"{self._login.iot.topic_prefix}/things/{self._login.blid}/{suffix}"

    def _filters(self) -> list[str]:
        return [
            f"$aws/things/{self._login.blid}/shadow/#",
            self._topic("cmd"),
            self._topic("rejected/report"),
            self._topic("mission/timeline/#"),
        ]

    def start(self) -> None:
        if not self._login.iot.topic_prefix:
            raise TransportUnavailable("the login cache has no cloud topic prefix")
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=self._login.iot.client_id,
            protocol=mqtt.MQTTv311,
            transport="websockets",
        )
        client.ws_set_options(
            path="/mqtt",
            headers={
                "x-amz-customauthorizer-name": self._login.iot.authorizer_name,
                "x-amz-customauthorizer-signature": self._login.iot.signature,
                "x-irobot-auth": self._login.iot.token,
            },
        )
        client.tls_set_context(ssl.create_default_context())
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client
        try:
            client.connect_async(self._login.iot.host, 443, keepalive=120)
            client.loop_start()
        except Exception as exc:
            self._client = None
            self._last_error = str(exc)
            raise TransportUnavailable(f"cannot start cloud MQTT: {exc}") from exc
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
            self._last_error = f"cloud MQTT connect refused: {reason_code.getName()}"
            log.warning(self._last_error)
            return
        self._connected = True
        self._last_error = ""
        result, _ = client.subscribe([(topic, 1) for topic in self._filters()])
        if result != mqtt.MQTT_ERR_SUCCESS:
            self._connected = False
            self._last_error = f"cloud SUBSCRIBE refused: {result.name}"

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
        topic = message.topic
        try:
            payload = json.loads(message.payload.decode("utf-8", "replace"))
        except ValueError:
            return
        if topic.endswith("/accepted") or topic.endswith("/rejected"):
            if isinstance(payload, dict):
                self._responses[topic] = payload
            self._event.set()
            return
        if "rejected" in topic:
            if isinstance(payload, dict):
                self._cache.set_rejection(payload)
            self._notify()
            return
        if "mission/timeline" in topic:
            if isinstance(payload, dict):
                self._cache.set_timeline(payload)
            self._notify()
            return
        reported = extract_reported(payload)
        if reported is None:
            return
        if "/shadow/name/" in topic:
            name = topic.split("/shadow/name/", 1)[1].split("/", 1)[0]
            self._cache.update_named(name, reported, "cloud")
            if name == self.current_shadow:
                # The classic cloud shadow has no live state; ro-currentstate is
                # the live document, so keep the current snapshot in sync.
                self._cache.update_current(reported, "cloud")
        else:
            # The classic cloud document carries identity only. Keep it apart
            # from the live-state clock.
            self._cache.update_classic(reported, "cloud")
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                log.exception("shadow change listener failed")

    def _publish(self, topic: str, payload: dict[str, Any], timeout: float) -> bool:
        client = self._client
        if client is None or not self.connected:
            raise TransportUnavailable("cloud transport is not connected")
        info = client.publish(topic, json.dumps(payload), qos=1)
        try:
            info.wait_for_publish(timeout)
        except (RuntimeError, ValueError) as exc:
            raise TransportUnavailable(f"cloud publish failed: {exc}") from exc
        return bool(info.is_published())

    def _await(self, accepted: str, rejected: str, timeout: float) -> tuple[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if accepted in self._responses:
                return "accepted", self._responses.pop(accepted)
            if rejected in self._responses:
                return "rejected", self._responses.pop(rejected)
            self._event.wait(0.1)
            self._event.clear()
        return "timeout", None

    def request_shadow(self, name: str | None, timeout: float) -> dict[str, Any]:
        blid = self._login.blid
        if name:
            get_topic = f"$aws/things/{blid}/shadow/name/{name}/get"
            accepted = get_topic + "/accepted"
            rejected = get_topic + "/rejected"
        else:
            get_topic = f"$aws/things/{blid}/shadow/get"
            accepted = get_topic + "/accepted"
            rejected = get_topic + "/rejected"
        self._responses.pop(accepted, None)
        self._responses.pop(rejected, None)
        if not self._publish(get_topic, {}, timeout=5.0):
            raise TransportUnavailable("the broker did not accept the shadow request")
        status, payload = self._await(accepted, rejected, timeout)
        if status != "accepted" or not isinstance(payload, dict):
            raise TransportTimeout(
                "the cloud did not return the requested shadow",
                detail={"transport": "cloud", "shadow": name or "classic", "status": status},
            )
        reported = extract_reported(payload)
        if reported is None:
            raise TransportTimeout(
                "the cloud shadow response has no reported state",
                detail={"transport": "cloud", "shadow": name or "classic"},
            )
        if name:
            self._cache.update_named(name, reported, "cloud")
            if name == self.current_shadow:
                self._cache.update_current(reported, "cloud")
        else:
            self._cache.update_classic(reported, "cloud")
        self._notify()
        return reported

    def write_command(self, payload: dict[str, Any], timeout: float) -> WriteResult:
        started_at = time.monotonic()
        if not self._publish(self._topic("cmd"), payload, timeout):
            return WriteResult("unknown", self.name, "publish was not confirmed")
        deadline = time.monotonic() + REJECTION_WINDOW_S
        while time.monotonic() < deadline:
            rejection = self._cache.rejection_since(started_at)
            if rejection is not None:
                return WriteResult("rejected", self.name, json.dumps(rejection)[:500])
            time.sleep(0.05)
        return WriteResult("sent", self.name)

    def write_settings(
        self, values: dict[str, Any], shadow: str, timeout: float
    ) -> WriteResult:
        blid = self._login.blid
        update = f"$aws/things/{blid}/shadow/name/{shadow}/update"
        status, payload = self._update_shadow(update, values, timeout)
        if status == "accepted":
            return WriteResult("sent", self.name)
        if status == "rejected":
            return WriteResult("rejected", self.name, json.dumps(payload)[:500])
        return WriteResult("unknown", self.name, "no shadow update acknowledgement")

    def _update_shadow(
        self, topic: str, values: dict[str, Any], timeout: float
    ) -> tuple[str, Any]:
        accepted = topic + "/accepted"
        rejected = topic + "/rejected"
        self._responses.pop(accepted, None)
        self._responses.pop(rejected, None)
        if not self._publish(topic, {"state": {"desired": values}}, timeout=5.0):
            return "unknown", None
        return self._await(accepted, rejected, min(timeout, ACK_TIMEOUT_S))

    def raw_publish(self, topic: str, payload: str) -> WriteResult:
        raise Unsupported("raw_publish is available on the local transport only")
