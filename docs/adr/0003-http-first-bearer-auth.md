# 0003: HTTP first, bearer token off loopback

## Context

The server has to be reachable from other machines on the LAN and has to run
in a container. A loopback-only default would make the container useless.
MCP clients differ: some speak streamable HTTP, some only stdio.

## Decision

- Streamable HTTP is the primary transport on port 8785.
- A loopback bind needs no token. Any other bind refuses to start unless a
  bearer token is available from `ROOMBA_HTTP_TOKEN` or the private
  `http-token` file written by `roomba-mcp token`.
- `/healthz` stays public so a container health check and a load balancer can
  probe it without credentials.
- stdio remains available with `roomba-mcp serve --stdio` for local clients.

## Consequences

LAN deployments are authenticated with one static token. There is no OAuth
flow and no user database. Rotating the token is `roomba-mcp token --force`
plus a restart.
