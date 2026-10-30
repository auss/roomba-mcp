# 0001: Local first, cloud fallback, reads may fail over

## Context

The robot speaks MQTT over TLS on the LAN (LSS) and also through AWS IoT when
the phone app is remote. The local path is faster and keeps working without
the cloud, but the robot sleeps and drops off the LAN. The cloud path needs a
login session that expires.

## Decision

One long-lived MQTT connection at a time, owned by a transport manager.

- `ROOMBA_TRANSPORT=auto` probes the robot with a one second TCP connect every
  sixty seconds and prefers local when it answers.
- A read that times out or finds the transport unavailable may be retried once
  over the other transport, because reads have no side effects.
- A write that has been handed to a transport is never replayed. A timeout is
  reported as `outcome: "unknown"` so the caller can check state before acting.
- Only one connection per transport is alive; switching stops the old one.

## Consequences

Reads survive a robot that is asleep or a laptop that left the LAN. Writes can
still report an unknown outcome, which is honest: the command may have
arrived. The probe is cheap and does not keep the robot awake.
