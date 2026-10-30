"""MCP server: tools, resources, the HTTP app and bearer auth.

The server exposes one intent per tool and never asks for confirmation: the
safety gate is the ``ROOMBA_ALLOW_WRITES`` environment flag, not a question a
model can answer. Reads are served from the shadow cache and refreshed on
demand; writes go through :class:`~roomba_mcp.service.RobotService`, which
logs every command as one JSON line.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.subscriptions import InMemorySubscriptionBus
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .auth import LoginCache, load_login
from .cloud_rest import CloudRest
from .config import Config, is_loopback
from .errors import ConfigError, RoombaError
from .notifier import ResourceNotifier
from .service import RobotService
from .shadow import ShadowCache
from .transports.manager import TransportManager

log = logging.getLogger(__name__)

SERVER_NAME = "roomba-mcp"
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"
T = TypeVar("T")


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


async def _call[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking service call off the event loop and map typed errors."""
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except RoombaError as exc:
        raise ToolError(json.dumps(exc.as_dict(), sort_keys=True)) from exc


@dataclass
class Runtime:
    config: Config
    login: LoginCache | None
    cache: ShadowCache
    link: TransportManager
    service: RobotService
    bus: InMemorySubscriptionBus
    notifier: ResourceNotifier

    @classmethod
    def build(cls, config: Config) -> Runtime:
        login = load_login(config.login_file)
        cache = ShadowCache()
        bus = InMemorySubscriptionBus()
        link = TransportManager(config, login, cache)
        rest = CloudRest(login) if login is not None else None
        service = RobotService(config=config, link=link, rest=rest, cache=cache)
        notifier = ResourceNotifier(service, bus)
        return cls(
            config=config,
            login=login,
            cache=cache,
            link=link,
            service=service,
            bus=bus,
            notifier=notifier,
        )

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": SERVER_NAME,
            "version": __version__,
            "transport": self.link.health(),
            "writes_enabled": _flag("ROOMBA_ALLOW_WRITES"),
            "raw_enabled": _flag("ROOMBA_ALLOW_RAW"),
            "experimental_enabled": _flag("ROOMBA_EXPERIMENTAL"),
        }


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


@asynccontextmanager
async def _lifespan(runtime: Runtime) -> AsyncIterator[Runtime]:
    runtime.link.start()
    task = asyncio.create_task(runtime.notifier.run(), name="roomba-notifier")
    try:
        yield runtime
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        runtime.link.stop()


def create_server(runtime: Runtime) -> MCPServer:
    """Register every tool, resource and route on a fresh server."""

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[Runtime]:
        async with _lifespan(runtime) as active:
            yield active

    server = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=(
            "Control and observe a Roomba Combo j9+. Reads are safe. Writes are "
            "disabled unless ROOMBA_ALLOW_WRITES=1. Check get_robot_info for the "
            "capability tiers before using a command."
        ),
        lifespan=lifespan,
        subscriptions=runtime.bus,
    )

    service = runtime.service

    # -- reads -------------------------------------------------------------

    @server.tool(
        name="get_status",
        description=(
            "Live robot state: battery, mission phase, bin, tank, pad, position "
            "and the raw reported shadow. Set refresh=true to bypass the cache."
        ),
    )
    async def get_status(refresh: bool = False) -> str:
        return _json(await _call(service.get_status, refresh=refresh))

    @server.tool(
        name="get_robot_info",
        description=(
            "Identity, active transport, login state, capability tiers and the "
            "current safety flags. Call this first to see what is allowed."
        ),
    )
    async def get_robot_info() -> str:
        return _json(await _call(service.get_robot_info))

    @server.tool(
        name="get_settings",
        description=(
            "Current cleaning settings: suction, carpet boost, edge clean, pad "
            "wetness, multi pass, bin pause and child lock."
        ),
    )
    async def get_settings(refresh: bool = False) -> str:
        return _json(await _call(service.get_settings, refresh=refresh))

    @server.tool(
        name="get_schedule",
        description="The stored cleaning schedule, including per-region parameters.",
    )
    async def get_schedule(refresh: bool = False) -> str:
        return _json(await _call(service.get_schedule, refresh=refresh))

    @server.tool(
        name="get_mission_history",
        description="Recent finished missions from the iRobot cloud (1 to 100).",
    )
    async def get_mission_history(limit: int = 20) -> str:
        return _json(await _call(service.get_mission_history, limit))

    @server.tool(
        name="get_consumables",
        description=(
            "Consumable counters: brush, filter, pads and other parts with the "
            "remaining life and whether a reset is due."
        ),
    )
    async def get_consumables() -> str:
        return _json(await _call(service.get_consumables))

    @server.tool(
        name="get_map",
        description="Smart maps with their regions, for use with start_clean.",
    )
    async def get_map(refresh: bool = False) -> str:
        return _json(await _call(service.get_map, refresh=refresh))

    # -- commands ----------------------------------------------------------

    @server.tool(
        name="find_robot",
        description="Make the robot play its locating sound. Needs ROOMBA_ALLOW_WRITES=1.",
    )
    async def find_robot() -> str:
        return _json(await _call(service.find_robot))

    @server.tool(
        name="start_clean",
        description=(
            "Start a whole-home clean. Region, pass, suction and edge arguments "
            "need ROOMBA_EXPERIMENTAL=1 and a map from get_map. Needs "
            "ROOMBA_ALLOW_WRITES=1."
        ),
    )
    async def start_clean(
        regions: list[str] | None = None,
        passes: int | None = None,
        suction: str | None = None,
        edge_clean: bool | None = None,
    ) -> str:
        return _json(
            await _call(
                service.start_clean,
                regions=regions,
                passes=passes,
                suction=suction,
                edge_clean=edge_clean,
            )
        )

    @server.tool(
        name="pause_clean",
        description="Pause the current mission. Needs ROOMBA_ALLOW_WRITES=1.",
    )
    async def pause_clean() -> str:
        return _json(await _call(service.pause_clean))

    @server.tool(
        name="resume_clean",
        description="Resume a paused mission. Needs ROOMBA_ALLOW_WRITES=1.",
    )
    async def resume_clean() -> str:
        return _json(await _call(service.resume_clean))

    @server.tool(
        name="stop_clean",
        description="Stop the current mission. Needs ROOMBA_ALLOW_WRITES=1.",
    )
    async def stop_clean() -> str:
        return _json(await _call(service.stop_clean))

    @server.tool(
        name="dock",
        description="Send the robot back to its dock. Needs ROOMBA_ALLOW_WRITES=1.",
    )
    async def dock() -> str:
        return _json(await _call(service.dock))

    @server.tool(
        name="set_setting",
        description=(
            "Change one setting: suction (normal|high), carpet_boost, edge_clean, "
            "multi_pass, bin_pause, child_lock (true|false), pad_wetness (1-3). "
            "Needs ROOMBA_ALLOW_WRITES=1."
        ),
    )
    async def set_setting(name: str, value: bool | int | str) -> str:
        return _json(await _call(service.set_setting, name, value))

    @server.tool(
        name="set_schedule",
        description=(
            "Replace the cleaning schedule. Each entry needs a start object "
            "(day, hour, min) and a cmdStr string. Needs ROOMBA_ALLOW_WRITES=1."
        ),
    )
    async def set_schedule(schedule: list[dict[str, Any]]) -> str:
        return _json(await _call(service.set_schedule, schedule))

    @server.tool(
        name="reset_consumable",
        description=(
            "Reset one consumable counter by part id from get_consumables. "
            "Needs ROOMBA_ALLOW_WRITES=1."
        ),
    )
    async def reset_consumable(part: str) -> str:
        return _json(await _call(service.reset_consumable, part))

    @server.tool(
        name="raw_publish",
        description=(
            "Debug escape hatch: publish a raw JSON payload to one local topic "
            "(cmd, delta, shadow, timeline, testcmd). Needs ROOMBA_ALLOW_RAW=1 "
            "and the local transport."
        ),
    )
    async def raw_publish(topic: str, payload: dict[str, Any] | list[Any] | str) -> str:
        return _json(await _call(service.raw_publish, topic, payload))

    # -- resources ---------------------------------------------------------

    @server.resource(
        "roomba://status",
        name="status",
        description="Live robot status as JSON.",
        mime_type="application/json",
    )
    async def status_resource() -> str:
        return _json(await _call(service.get_status))

    @server.resource(
        "roomba://mission",
        name="mission",
        description="Current mission phase, counters and the latest timeline.",
        mime_type="application/json",
    )
    async def mission_resource() -> str:
        return _json(await _call(service.get_mission))

    @server.resource(
        "roomba://settings",
        name="settings",
        description="Current cleaning settings as JSON.",
        mime_type="application/json",
    )
    async def settings_resource() -> str:
        return _json(await _call(service.get_settings))

    @server.resource(
        "roomba://schedule",
        name="schedule",
        description="Stored cleaning schedule as JSON.",
        mime_type="application/json",
    )
    async def schedule_resource() -> str:
        return _json(await _call(service.get_schedule))

    # -- HTTP --------------------------------------------------------------

    @server.custom_route(HEALTH_PATH, methods=["GET"])
    async def healthz(request: Request) -> Response:
        return JSONResponse(runtime.health())

    return server


def resolve_http_token(config: Config) -> str | None:
    """Return the bearer token, or ``None`` on a loopback-only server."""
    token = os.environ.get("ROOMBA_HTTP_TOKEN", "").strip()
    if not token and config.token_file.exists():
        token = config.token_file.read_text(encoding="utf-8").strip()
    if token:
        return token
    if not is_loopback(config.http.host):
        raise ConfigError(
            "http.host is not loopback, so a bearer token is required",
            detail={
                "hint": "run 'roomba-mcp token' or set ROOMBA_HTTP_TOKEN",
                "host": config.http.host,
            },
        )
    return None


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>`` except on the health route."""

    def __init__(self, app: ASGIApp, token: str, exempt: tuple[str, ...] = (HEALTH_PATH,)) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode()
        self._exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"authorization", b"")
        if not secrets.compare_digest(supplied, self._expected):
            response = JSONResponse(
                {"ok": False, "error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def build_http_app(server: MCPServer, *, host: str, token: str | None) -> ASGIApp:
    app: ASGIApp = server.streamable_http_app(host=host)
    if token:
        app = BearerAuthMiddleware(app, token)
    return app


def write_token(path: Path) -> str:
    from .config import write_private_text

    token = secrets.token_urlsafe(32)
    write_private_text(path, token + "\n")
    return token


def token_is_set(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())
