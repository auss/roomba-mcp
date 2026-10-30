# 0005: Capability tiers instead of a single "supported" claim

## Context

Some behaviour was observed on this robot and firmware, some was derived from
the phone app and is low risk, and some is a guess about a physical action.
Treating all three as equal either blocks useful reads or invites a bad write.

## Decision

Every capability is labelled:

- `verified`: observed working against this robot and firmware.
- `derived-low-risk`: reverse engineered from the app, reversible, allowed by
  default but still behind the writes flag.
- `unverified-physical`: the wire format is unproven and a wrong payload can
  move the robot. Hard error unless `ROOMBA_EXPERIMENTAL=1`.

`get_robot_info` returns the whole table plus the current safety flags, so a
bot can decide before acting. Reads never hard-error on tier.

## Consequences

The tier names appear in tool output and are part of the public interface.
When a capability is verified on new firmware, its tier and note change in one
place: `capabilities.py`.
