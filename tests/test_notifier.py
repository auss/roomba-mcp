from typing import Any

import pytest
from mcp.server.subscriptions import InMemorySubscriptionBus

from roomba_mcp.notifier import ResourceNotifier


class FakeBus:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event.uri)


class FakeService:
    def __init__(self) -> None:
        self.payloads: dict[str, dict[str, Any]] = {
            "roomba://status": {"battery_pct": 90},
            "roomba://mission": {"phase": "run"},
        }

    def resource_payloads(self) -> dict[str, dict[str, Any]]:
        return self.payloads


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_notifier() -> tuple[ResourceNotifier, FakeBus, FakeService, FakeClock]:
    bus = FakeBus()
    service = FakeService()
    clock = FakeClock()
    notifier = ResourceNotifier(service, bus, min_interval_s=1.0, clock=clock)  # type: ignore[arg-type]
    return notifier, bus, service, clock


@pytest.mark.asyncio
async def test_initial_state_does_not_fire() -> None:
    notifier, bus, _service, _clock = make_notifier()
    assert await notifier.poll_once() == []
    assert bus.events == []


@pytest.mark.asyncio
async def test_change_fires_once_per_uri() -> None:
    notifier, bus, service, _clock = make_notifier()
    await notifier.poll_once()
    service.payloads["roomba://status"] = {"battery_pct": 89}
    assert await notifier.poll_once() == ["roomba://status"]
    assert bus.events == ["roomba://status"]
    # Same document again: no event.
    assert await notifier.poll_once() == []


@pytest.mark.asyncio
async def test_debounce_holds_changes_for_one_second() -> None:
    notifier, bus, service, clock = make_notifier()
    await notifier.poll_once()
    service.payloads["roomba://status"] = {"battery_pct": 88}
    assert await notifier.poll_once() == ["roomba://status"]

    service.payloads["roomba://status"] = {"battery_pct": 87}
    clock.now += 0.5
    assert await notifier.poll_once() == []

    clock.now += 0.6
    assert await notifier.poll_once() == ["roomba://status"]
    assert bus.events == ["roomba://status", "roomba://status"]


@pytest.mark.asyncio
async def test_real_bus_delivers_events_to_listeners() -> None:
    bus = InMemorySubscriptionBus()
    received: list[str] = []
    bus.subscribe(lambda event: received.append(event.uri))
    service = FakeService()
    notifier = ResourceNotifier(service, bus, min_interval_s=1.0, clock=FakeClock())
    await notifier.poll_once()
    service.payloads["roomba://status"] = {"battery_pct": 1}
    await notifier.poll_once()
    assert received == ["roomba://status"]


@pytest.mark.asyncio
async def test_age_changes_do_not_fire() -> None:
    notifier, bus, service, _clock = make_notifier()
    await notifier.poll_once()
    service.payloads["roomba://status"] = {"battery_pct": 90, "age_s": 0.1}
    assert await notifier.poll_once() == []
    service.payloads["roomba://status"] = {"battery_pct": 90, "age_s": 0.9}
    assert await notifier.poll_once() == []
    assert bus.events == []


@pytest.mark.asyncio
async def test_uris_are_debounced_independently() -> None:
    notifier, bus, service, clock = make_notifier()
    await notifier.poll_once()
    service.payloads["roomba://status"] = {"battery_pct": 88}
    assert await notifier.poll_once() == ["roomba://status"]

    service.payloads["roomba://mission"] = {"phase": "dock"}
    clock.now += 0.1
    assert await notifier.poll_once() == ["roomba://mission"]
    assert set(bus.events) == {"roomba://status", "roomba://mission"}
