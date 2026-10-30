import asyncio
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from roomba_mcp.config import Config, HttpConfig, LoggingConfig, RobotConfig, TransportConfig
from roomba_mcp.errors import ConfigError
from roomba_mcp.server import (
    Runtime,
    build_http_app,
    create_server,
    resolve_http_token,
    token_is_set,
    write_token,
)


def make_config(tmp_path: Path, host: str = "127.0.0.1") -> Config:
    return Config(
        robot=RobotConfig(blid="0123456789ABCDEF0123456789ABCDEF"),
        transport=TransportConfig(mode="local"),
        http=HttpConfig(host=host, port=8785),
        logging=LoggingConfig(),
        directory=tmp_path,
    )


def test_registers_seventeen_tools_and_four_resources(tmp_path: Path) -> None:
    runtime = Runtime.build(make_config(tmp_path))
    server = create_server(runtime)

    async def collect() -> tuple[list[str], list[str]]:
        tools = await server.list_tools()
        resources = await server.list_resources()
        return [tool.name for tool in tools], [str(resource.uri) for resource in resources]

    tools, resources = asyncio.run(collect())
    assert len(tools) == 17
    assert resources == [
        "roomba://status",
        "roomba://mission",
        "roomba://settings",
        "roomba://schedule",
    ]


def test_server_publishes_through_the_runtime_bus(tmp_path: Path) -> None:
    runtime = Runtime.build(make_config(tmp_path))
    server = create_server(runtime)
    # The notifier publishes on runtime.bus; the listen handler must use the
    # same instance or notifications go nowhere.
    assert server._subscriptions is runtime.bus


def test_healthz_is_public(tmp_path: Path) -> None:
    runtime = Runtime.build(make_config(tmp_path))
    server = create_server(runtime)
    app = build_http_app(server, host="127.0.0.1", token=None)
    with TestClient(app, base_url="http://localhost") as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["service"] == "roomba-mcp"
        assert body["transport"]["login_present"] is False


def test_bearer_token_gates_mcp(tmp_path: Path) -> None:
    runtime = Runtime.build(make_config(tmp_path))
    server = create_server(runtime)
    app = build_http_app(server, host="127.0.0.1", token="s3cret")
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/mcp").status_code == 401
        assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
        allowed = client.get("/mcp", headers={"Authorization": "Bearer s3cret"})
        assert allowed.status_code != 401


def test_non_loopback_requires_a_token(tmp_path: Path) -> None:
    config = make_config(tmp_path, host="0.0.0.0")
    with pytest.raises(ConfigError):
        resolve_http_token(config)


def test_token_file_unblocks_non_loopback(tmp_path: Path) -> None:
    config = make_config(tmp_path, host="0.0.0.0")
    token = write_token(config.token_file)
    assert token_is_set(config.token_file)
    assert resolve_http_token(config) == token
    assert config.token_file.stat().st_mode & 0o777 == 0o600
