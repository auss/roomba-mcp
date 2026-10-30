import time

from roomba_mcp.shadow import (
    ShadowCache,
    Snapshot,
    extract_desired,
    extract_reported,
)


def test_extract_reported_and_desired() -> None:
    payload = {"state": {"reported": {"batPct": 90}, "desired": {"vacHigh": True}}}
    assert extract_reported(payload) == {"batPct": 90}
    assert extract_desired(payload) == {"vacHigh": True}
    assert extract_reported({"state": {}}) is None
    assert extract_reported("nope") is None


def test_current_merges_and_classic_stays_separate() -> None:
    cache = ShadowCache()
    cache.update_current({"batPct": 90}, "local")
    cache.update_current({"phase": "run"}, "local")
    assert cache.current().reported == {"batPct": 90, "phase": "run"}
    assert cache.classic().reported == {}

    cache.update_classic({"sku": "c975840"}, "cloud")
    assert cache.classic().reported == {"sku": "c975840"}
    assert "sku" not in cache.current().reported


def test_named_shadows_are_independent() -> None:
    cache = ShadowCache()
    cache.update_named("rw-settings", {"vacHigh": True}, "cloud")
    cache.update_named("rw-schedule", {"cleanSchedule2": []}, "cloud")
    assert cache.named("rw-settings").reported == {"vacHigh": True}
    assert cache.named("rw-schedule").reported == {"cleanSchedule2": []}
    assert cache.named("missing") is None


def test_snapshot_staleness() -> None:
    fresh = Snapshot(reported={"a": 1}, updated_at=time.monotonic())
    assert not fresh.is_stale(15)
    old = Snapshot(reported={"a": 1}, updated_at=1.0)
    assert old.is_stale(15, now=100.0)
    assert Snapshot().age_s() == float("inf")


def test_rejection_window() -> None:
    cache = ShadowCache()
    started = time.monotonic()
    assert cache.rejection_since(started) is None
    cache.set_rejection({"error": "busy"})
    assert cache.rejection_since(started) == {"error": "busy"}
    assert cache.rejection_since(started + 10) is None


def test_timeline_is_stored_with_timestamp() -> None:
    cache = ShadowCache()
    assert cache.timeline() == (None, 0.0)
    cache.set_timeline({"event": []})
    timeline, at = cache.timeline()
    assert timeline == {"event": []}
    assert at > 0
