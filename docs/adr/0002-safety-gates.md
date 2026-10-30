# 0002: Writes are gated by the environment, not by prompts

## Context

An MCP tool is called by a model. A confirmation argument such as
`confirm: true` is not a safety boundary: the model can supply it. A setting
in a copied configuration file can also be flipped by accident.

## Decision

- Reads need no flag.
- Every command needs `ROOMBA_ALLOW_WRITES=1` in the server environment.
- `raw_publish` additionally needs `ROOMBA_ALLOW_RAW=1` and the local
  transport.
- Parameterized `start_clean` (regions, passes, suction, edge clean) needs
  `ROOMBA_EXPERIMENTAL=1` because the immediate command shape is unverified on
  this firmware.
- Safety flags are read from the environment only. They are not accepted in
  `config.toml` or on the command line, so a copied config cannot enable
  writes.

## Consequences

Turning writes on is a deliberate deployment action. There are no confirmation
arguments for a model to guess, and every command is logged as one JSON line
with the tool, payload, transport and result.
