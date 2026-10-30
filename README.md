# roomba-mcp

An MCP server for a Roomba Combo j9+ (`stingray`, sku `c975840`). It controls
and observes the robot over the local LSS protocol and falls back to the iRobot
cloud when the robot is off the LAN.

Tested against firmware `stingray+24.29.5`. Not affiliated with iRobot.

## Scope

In scope:

- mission control: start, pause, resume, stop, dock, find
- live state, settings, schedule, mission history
- consumables, smart maps and regions

Out of scope, deliberately:

- OTA and firmware endpoints
- account, team, household transfer and notification endpoints
- image upload approvals
- the account reset endpoint
- manual driving

## Safety model

- Reads need no flag.
- Every command needs `ROOMBA_ALLOW_WRITES=1` in the server environment.
- `raw_publish` also needs `ROOMBA_ALLOW_RAW=1` and the local transport.
- Region, pass, suction and edge arguments to `start_clean` need
  `ROOMBA_EXPERIMENTAL=1`, because the immediate command shape is unverified
  on this firmware.
- There are no confirmation arguments. The gate is the environment, not a
  question a model can answer.
- Every command is logged as one JSON line with the tool, payload, transport
  and result.
- A write that times out is reported as `outcome: "unknown"` and is never
  replayed over the other transport.

## Requirements

- Python 3.12 or newer, or Docker
- `uv` for the source install
- The robot on the same LAN for local control
- An iRobot account for the first login and for cloud reads

## Install

```bash
git clone https://github.com/auss/roomba-mcp.git
cd roomba-mcp
uv sync
uv run roomba-mcp version
```

## Login

`login` is the only command that writes the login cache. The server never logs
in by itself.

```bash
uv run roomba-mcp login
```

The command prompts for the account email and password, discovers the current
deployment, and writes `~/.config/roomba-mcp/login.json` with mode `0600`. Pass
`--blid` when the account owns several robots.

The login cache holds the robot password, the local MQTT credentials and a
temporary AWS session. When the session expires, cloud reads return
`login_expired` and you run `login` again.

## Configuration

Two files in `~/.config/roomba-mcp` (or `$XDG_CONFIG_HOME/roomba-mcp`):

| file | mode | purpose |
| --- | --- | --- |
| `config.toml` | 0644 | non-secret settings |
| `login.json` | 0600 | account session and robot password |
| `http-token` | 0600 | bearer token for a non-loopback bind |

```toml
[robot]
blid = "0123456789ABCDEF0123456789ABCDEF"
host = "192.168.1.27"
port = 8883

[http]
host = "0.0.0.0"
port = 8785

[transport]
mode = "auto"

[logging]
level = "info"
```

Precedence is defaults, then `config.toml`, then the environment, then command
line flags.

Environment variables:

| variable | default | meaning |
| --- | --- | --- |
| `ROOMBA_CONFIG_DIR` | `~/.config/roomba-mcp` | config directory |
| `ROOMBA_CONFIG` | `<dir>/config.toml` | explicit config file |
| `ROOMBA_BLID` | from login cache | robot id |
| `ROOMBA_HOST` | `irobot-<blid>.local` | robot host or IP |
| `ROOMBA_ROBOT_PORT` | `8883` | local MQTT port |
| `ROOMBA_TRANSPORT` | `auto` | `auto`, `local` or `cloud` |
| `ROOMBA_HTTP_HOST` | `127.0.0.1` | HTTP bind address |
| `ROOMBA_HTTP_PORT` | `8785` | HTTP port |
| `ROOMBA_HTTP_TOKEN` | from `http-token` | bearer token |
| `ROOMBA_LOG_LEVEL` | `info` | log level |
| `ROOMBA_LOG_FILE` | none | extra log file |
| `ROOMBA_ALLOW_WRITES` | off | enable commands |
| `ROOMBA_ALLOW_RAW` | off | enable `raw_publish` |
| `ROOMBA_EXPERIMENTAL` | off | enable unverified parameters |

Safety flags are read from the environment only. They are not accepted in
`config.toml`, so a copied config cannot turn writes on by accident.

## Run

HTTP is the primary transport:

```bash
uv run roomba-mcp serve --http-host 0.0.0.0 --http-port 8785
```

A non-loopback bind refuses to start without a token. Create one once:

```bash
uv run roomba-mcp token
```

The token is printed and written to `http-token` with mode `0600`. Rotate it
with `roomba-mcp token --force` and restart the server.

Check the server:

```bash
uv run roomba-mcp healthcheck
curl -H "Authorization: Bearer <token>" http://192.168.1.25:8785/healthz
```

stdio is available for local clients:

```bash
uv run roomba-mcp serve --stdio
```

### Client configuration

HTTP:

```json
{
  "mcpServers": {
    "roomba": {
      "type": "http",
      "url": "http://192.168.1.25:8785/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

stdio:

```json
{
  "mcpServers": {
    "roomba": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/roomba-mcp", "roomba-mcp", "serve", "--stdio"]
    }
  }
}
```

## Docker

Run `login` and `token` on the host first, then start the container. The
config directory is mounted read-only: the container never writes it.

```bash
uv run roomba-mcp login
uv run roomba-mcp token
ROOMBA_HOST=192.168.1.27 docker compose up -d --build
curl http://127.0.0.1:8785/healthz
```

`compose.yaml` uses host networking, which keeps local discovery simple.
`compose.bridge.yaml` publishes port 8785 instead:

```bash
ROOMBA_HOST=192.168.1.27 docker compose -f compose.bridge.yaml up -d --build
```

Both files run the container as your host uid so the `0600` login cache is
readable. If your ids are not `1000`, set `ROOMBA_UID` and `ROOMBA_GID`.

Set `ROOMBA_HOST` to the robot IP when mDNS does not resolve inside the
container.

## Tools

| tool | kind | needs | notes |
| --- | --- | --- | --- |
| `get_status` | read | | battery, phase, bin, tank, pad, position |
| `get_robot_info` | read | | identity, transport, capability tiers, flags |
| `get_settings` | read | | suction, carpet boost, edge clean, pad wetness |
| `get_schedule` | read | | schedule entries and per-region parameters |
| `get_mission_history` | read | login | cloud REST, 1 to 100 missions |
| `get_consumables` | read | login | cloud REST, part counters |
| `get_map` | read | login | cloud REST, smart maps and regions |
| `find_robot` | write | writes | locating sound |
| `start_clean` | write | writes | plain start by default |
| `pause_clean` | write | writes | |
| `resume_clean` | write | writes | |
| `stop_clean` | write | writes | |
| `dock` | write | writes | |
| `set_setting` | write | writes | typed subset only |
| `set_schedule` | write | writes | replaces the schedule |
| `reset_consumable` | write | writes, login | cloud REST |
| `raw_publish` | raw | raw, local | debug topics only |

`start_clean` accepts `regions`, `passes`, `suction` and `edge_clean` only with
`ROOMBA_EXPERIMENTAL=1`. Region ids come from `get_map`. A region clean sends
the same shape the schedule shadow stores.

## Resources

| URI | contents |
| --- | --- |
| `roomba://status` | live status |
| `roomba://mission` | current mission and timeline |
| `roomba://settings` | current settings |
| `roomba://schedule` | stored schedule |

The server publishes `notifications/resources/updated` through the MCP
`subscriptions/listen` stream. Notifications are debounced to at most one per
URI per second, deduplicated by payload hash, and never fired for the initial
state. Reads carry `stale` and `age_s` so a client can tell cached data from
live data.

## Transports

| mode | behaviour |
| --- | --- |
| `auto` | probe local every 60 s, prefer local, fall back to cloud |
| `local` | local LSS only |
| `cloud` | AWS IoT MQTT only |

The local connection uses TLS with the iRobot certificate, the legacy
`AES128-SHA256` cipher at `@SECLEVEL=0`, and
`SSL_OP_LEGACY_SERVER_CONNECT`. The cloud connection uses the AWS IoT
WebSocket endpoint with the authorizer headers from the login cache. One
connection is alive at a time.

Cloud reads use named shadows: `ro-currentstate` for live state,
`rw-settings` for settings and `rw-schedule` for the schedule. The classic
cloud shadow only carries identity and capability fields.

## Capability tiers

`get_robot_info` reports a tier per capability:

- `verified`: observed working on this robot and firmware.
- `derived-low-risk`: reverse engineered from the app, reversible, still
  behind the writes flag.
- `unverified-physical`: unproven wire format, hard error without
  `ROOMBA_EXPERIMENTAL=1`.

## Development

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
```

`pre-commit` runs ruff, an em-dash check and `gitleaks` on every commit.
Install it once:

```bash
uv run pre-commit install
```

CI runs lint, types, unit tests, CodeQL, dependency review, a secret scan and
the em-dash check. It never talks to a robot.

See [CONTRIBUTING.md](CONTRIBUTING.md) for contributions and
[SECURITY.md](SECURITY.md) for private vulnerability reports and safe deployment.

Tests use sanitized captures from the real robot. See
`tests/fixtures/README.md`.

Design notes live in `docs/adr/`. Protocol references are vendored in
`docs/protocol/` with provenance headers.

## License

MIT. See `LICENSE`.
