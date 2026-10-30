# 0004: Four JSON resources with debounced notifications

## Context

A bot that polls `get_status` in a loop wastes tokens and hides staleness.
MCP has resource subscriptions, but the current SDK serves them through
`subscriptions/listen` and the client must opt in per URI.

## Decision

- Expose `roomba://status`, `roomba://mission`, `roomba://settings` and
  `roomba://schedule` as JSON resources.
- A background notifier polls the shadow cache every 250 ms, hashes each
  payload and publishes `notifications/resources/updated` only when the
  document changed, at most once per URI per second.
- The first observed document is a baseline, not an event: subscribers read
  the resource when they subscribe.
- Reads report `stale` and `age_s` so a client can tell cached data from live
  data.

## Consequences

Idle robots produce no traffic. A busy mission produces at most one event per
second per resource. Payload hashes mean a field that flips back and forth
within one second is coalesced, which is acceptable for a cleaning robot.
