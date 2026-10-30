# AGENTS.md

MCP server for a Roomba Combo j9+ (`stingray`, sku `c975840`): local LSS
control with AWS IoT cloud fallback. Python + official MCP SDK, managed with
`uv`.

## Where things live

- User docs: `README.md`. Terminology: `GLOSSARY.md`.
- Design decisions: `docs/adr/` (0001 transport failover, 0002 safety gates,
  0003 http-first bearer auth, 0004 resource notifications, 0005 capability
  tiers). Read the relevant ADR before changing transport, auth, or safety.
- Vendored protocol references: `docs/protocol/`. Treat as source-of-truth
  reference, not spec.
- Test fixtures: `tests/fixtures/` (sanitized captures; see its `README.md`).

## Non-obvious rules

- The server never logs in. `roomba-mcp login` is the only writer of the login
  cache; the server only reads it.
- Safety flags (`ROOMBA_ALLOW_WRITES`, `ROOMBA_ALLOW_RAW`,
  `ROOMBA_EXPERIMENTAL`) are environment-only, never in `config.toml`.
- Writes fail closed: a timed-out command reports `outcome: "unknown"` and is
  never replayed over the other transport.
- Cloud shadows are split: `ro-currentstate` = live state, `rw-settings` =
  settings, `rw-schedule` = schedule, classic shadow = identity only. Do not
  merge the cloud classic shadow into the live-state cache.
- Resource notifications use the MCP `subscriptions/listen` stream (pass
  `subscriptions=runtime.bus` to `MCPServer`); the legacy `resources/subscribe`
  is not served.
- The notifier strips the volatile `age_s` / `timeline_age_s` fields before
  hashing, so a poll that only advances time does not fire a notification.

## Checks

Run before committing:

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest -q
```

`pre-commit` runs ruff, the em-dash check and `gitleaks`. Install it once with
`uv run pre-commit install`.
