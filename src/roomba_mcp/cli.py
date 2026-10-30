"""Command line entry point.

``login`` is the only command that writes ``login.json``. ``serve`` never logs
in by itself: when the cache is missing or expired it reports the condition
and the operator runs ``login`` again.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import __version__
from .auth import login as account_login
from .auth import save_login
from .config import Config, config_dir, load_config
from .errors import RoombaError
from .logging import configure
from .server import (
    HEALTH_PATH,
    build_http_app,
    create_server,
    resolve_http_token,
    write_token,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roomba-mcp",
        description="MCP server for a Roomba Combo j9+ (local LSS, cloud fallback).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=None,
        help="config directory (default: $XDG_CONFIG_HOME/roomba-mcp)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="exchange account credentials for a robot password")
    login.add_argument("--email", help="iRobot account email (prompted when omitted)")
    login.add_argument("--blid", help="robot id when the account has several robots")
    login.add_argument("--country", default="US", help="discovery country code (default: US)")

    token = sub.add_parser("token", help="create or print the HTTP bearer token")
    token.add_argument("--force", action="store_true", help="replace an existing token")

    serve = sub.add_parser("serve", help="run the MCP server")
    serve.add_argument(
        "--stdio",
        action="store_true",
        help="serve over stdio instead of streamable HTTP",
    )
    serve.add_argument("--http-host", help="HTTP bind address")
    serve.add_argument("--http-port", type=int, help="HTTP port (default: 8785)")
    serve.add_argument("--robot-host", help="robot host or IP")
    serve.add_argument("--robot-port", type=int, help="robot MQTT port (default: 8883)")
    serve.add_argument("--blid", help="robot id")
    serve.add_argument(
        "--transport",
        choices=("auto", "local", "cloud"),
        help="transport preference (default: auto)",
    )

    health = sub.add_parser("healthcheck", help="call /healthz on a running server")
    health.add_argument("--url", help="health URL (default: from config)")
    health.add_argument("--token", help="bearer token (default: config or env)")

    sub.add_parser("version", help="print the version")
    return parser


def _config(args: argparse.Namespace, **overrides: Any) -> Config:
    directory = args.config_dir.expanduser() if args.config_dir else config_dir()
    return load_config(directory=directory, **overrides)


def _cmd_login(args: argparse.Namespace) -> int:
    config = _config(args, blid=args.blid)
    email = args.email or input("iRobot email: ").strip()
    if not email:
        print("error: an email is required", file=sys.stderr)
        return 2
    password = getpass.getpass("iRobot password: ")
    if not password:
        print("error: a password is required", file=sys.stderr)
        return 2
    cache = account_login(email, password, blid=args.blid, country=args.country)
    save_login(config.login_file, cache)
    expires = cache.expires_at()
    print(
        json.dumps(
            {
                "ok": True,
                "login_file": str(config.login_file),
                "blid": cache.blid,
                "robot_name": cache.robot_name,
                "expires_at": expires.isoformat() if expires else None,
            },
            indent=2,
        )
    )
    return 0


def _cmd_token(args: argparse.Namespace) -> int:
    config = _config(args)
    path = config.token_file
    if path.exists() and not args.force:
        token = path.read_text(encoding="utf-8").strip()
    else:
        token = write_token(path)
    print(
        json.dumps(
            {"ok": True, "token_file": str(path), "token": token},
            indent=2,
        )
    )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    config = _config(
        args,
        host=args.robot_host,
        port=args.robot_port,
        blid=args.blid,
        http_host=args.http_host,
        http_port=args.http_port,
    )
    if args.transport:
        config = replace(
            config,
            transport=replace(config.transport, mode=args.transport),
        )
    configure(config.logging.level, config.logging.file)

    from .server import Runtime

    runtime = Runtime.build(config)
    server = create_server(runtime)

    if args.stdio:
        server.run(transport="stdio")
        return 0

    import uvicorn

    token = resolve_http_token(config)
    app = build_http_app(server, host=config.http.host, token=token)
    uvicorn.run(
        app,
        host=config.http.host,
        port=config.http.port,
        log_level=config.logging.level.lower(),
    )
    return 0


def _cmd_healthcheck(args: argparse.Namespace) -> int:
    config = _config(args)
    url = args.url or f"http://{config.http.host}:{config.http.port}{HEALTH_PATH}"
    token = args.token or resolve_http_token(config)
    request = urllib.request.Request(url, method="GET")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            body = response.read().decode("utf-8", "replace")
            print(body)
            return 0 if response.status == 200 else 1
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", "replace"), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"error: cannot reach {url}: {exc.reason}", file=sys.stderr)
        return 1


def _cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {
        "login": _cmd_login,
        "token": _cmd_token,
        "serve": _cmd_serve,
        "healthcheck": _cmd_healthcheck,
        "version": _cmd_version,
    }
    try:
        return handlers[args.command](args)
    except RoombaError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        if exc.detail is not None:
            print(json.dumps(exc.detail, indent=2, default=str), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
