"""Resource change notifications.

The server polls the shadow cache on a short interval and publishes
``notifications/resources/updated`` through the MCP subscription bus. Three
rules keep the stream calm:

* payload hash dedupe - an unchanged document never fires;
* at most one event per URI per second;
* no event for the initial state, because subscribers read resources when
  they subscribe.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server.subscriptions import InMemorySubscriptionBus, ResourceUpdated

from .service import RobotService

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.25
MIN_EVENT_INTERVAL_S = 1.0


VOLATILE_KEYS = frozenset({"age_s", "timeline_age_s"})


def stable_payload(value: Any) -> Any:
    """Drop fields that change on every poll but describe no state change."""
    if isinstance(value, dict):
        return {
            key: stable_payload(item)
            for key, item in value.items()
            if key not in VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [stable_payload(item) for item in value]
    return value


def payload_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        stable_payload(payload),
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResourceNotifier:
    def __init__(
        self,
        service: RobotService,
        bus: InMemorySubscriptionBus,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        min_interval_s: float = MIN_EVENT_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._bus = bus
        self._poll_interval_s = poll_interval_s
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._digests: dict[str, str] = {}
        self._last_sent: dict[str, float] = {}
        self._primed = False

    async def poll_once(self) -> list[str]:
        """Publish at most one event per changed URI and return their URIs."""
        payloads = self._service.resource_payloads()
        now = self._clock()
        published: list[str] = []
        for uri, payload in payloads.items():
            digest = payload_digest(payload)
            if digest == self._digests.get(uri):
                continue
            if not self._primed:
                self._digests[uri] = digest
                continue
            if now - self._last_sent.get(uri, float("-inf")) < self._min_interval_s:
                continue
            self._digests[uri] = digest
            self._last_sent[uri] = now
            await self._bus.publish(ResourceUpdated(uri=uri))
            published.append(uri)
        self._primed = True
        return published

    async def run(self) -> None:
        """Poll until cancelled by the server lifespan."""
        while True:
            try:
                await self.poll_once()
            except Exception:  # a read failure must not stop notifications
                log.exception("resource poll failed")
            await asyncio.sleep(self._poll_interval_s)
