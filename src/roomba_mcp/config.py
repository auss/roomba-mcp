"""Configuration and on-disk paths.

Two files, two trust levels:

* ``config.toml`` (0644) - non-secret settings, safe to read and copy.
* ``login.json`` (0600) - account session, robot password, cloud tokens. Only
  ``roomba-mcp login`` writes it.

Environment overrides win over the file; CLI flags win over both. Safety
flags exist only in the environment so a copied config file can never turn
writes on by accident.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 8785
DEFAULT_ROBOT_PORT = 8883
TRANSPORT_MODES = ("auto", "local", "cloud")


def config_dir() -> Path:
    override = os.environ.get("ROOMBA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "roomba-mcp"


def config_path(directory: Path | None = None) -> Path:
    override = os.environ.get("ROOMBA_CONFIG")
    if override:
        return Path(override).expanduser()
    return (directory or config_dir()) / "config.toml"


def login_path(directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "login.json"


def token_path(directory: Path | None = None) -> Path:
    return (directory or config_dir()) / "http-token"


@dataclass(frozen=True)
class RobotConfig:
    blid: str = ""
    host: str = ""
    port: int = DEFAULT_ROBOT_PORT

    @property
    def resolved_host(self) -> str:
        return self.host or f"irobot-{self.blid.lower()}.local"


@dataclass(frozen=True)
class TransportConfig:
    mode: str = "auto"
    probe_interval_s: float = 60.0
    probe_timeout_s: float = 1.0
    read_timeout_s: float = 8.0
    connect_timeout_s: float = 10.0


@dataclass(frozen=True)
class HttpConfig:
    host: str = DEFAULT_HTTP_HOST
    port: int = DEFAULT_HTTP_PORT


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "info"
    file: str = ""


@dataclass(frozen=True)
class Config:
    robot: RobotConfig
    transport: TransportConfig
    http: HttpConfig
    logging: LoggingConfig
    directory: Path

    @property
    def login_file(self) -> Path:
        return login_path(self.directory)

    @property
    def token_file(self) -> Path:
        return token_path(self.directory)

    @property
    def config_file(self) -> Path:
        return config_path(self.directory)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a TOML table")
    return data


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def _env_str(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _peek_blid(directory: Path) -> str:
    """Read only the robot id from the login cache, never the secrets."""
    path = login_path(directory)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    value = data.get("blid") if isinstance(data, dict) else None
    return value if isinstance(value, str) else ""


def load_config(
    *,
    directory: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    blid: str | None = None,
    http_host: str | None = None,
    http_port: int | None = None,
) -> Config:
    """Load defaults, then config.toml, then env, then explicit overrides."""
    directory = (directory or config_dir()).expanduser()
    data = _read_toml(config_path(directory))
    robot = _section(data, "robot")
    transport = _section(data, "transport")
    http = _section(data, "http")
    logging = _section(data, "logging")

    robot_cfg = RobotConfig(
        blid=str(robot.get("blid", "")),
        host=str(robot.get("host", "")),
        port=int(robot.get("port", DEFAULT_ROBOT_PORT)),
    )
    transport_cfg = TransportConfig(
        mode=str(transport.get("mode", "auto")),
        probe_interval_s=float(transport.get("probe_interval_s", 60.0)),
        probe_timeout_s=float(transport.get("probe_timeout_s", 1.0)),
        read_timeout_s=float(transport.get("read_timeout_s", 8.0)),
        connect_timeout_s=float(transport.get("connect_timeout_s", 10.0)),
    )
    http_cfg = HttpConfig(
        host=str(http.get("host", DEFAULT_HTTP_HOST)),
        port=int(http.get("port", DEFAULT_HTTP_PORT)),
    )
    logging_cfg = LoggingConfig(
        level=str(logging.get("level", "info")),
        file=str(logging.get("file", "")),
    )

    if env_blid := _env_str("ROOMBA_BLID"):
        robot_cfg = replace(robot_cfg, blid=env_blid)
    if env_host := _env_str("ROOMBA_HOST"):
        robot_cfg = replace(robot_cfg, host=env_host)
    if _env_str("ROOMBA_ROBOT_PORT"):
        robot_cfg = replace(robot_cfg, port=_env_int("ROOMBA_ROBOT_PORT", robot_cfg.port))
    if env_mode := _env_str("ROOMBA_TRANSPORT"):
        transport_cfg = replace(transport_cfg, mode=env_mode)
    transport_cfg = replace(
        transport_cfg,
        probe_interval_s=_env_float("ROOMBA_PROBE_INTERVAL_S", transport_cfg.probe_interval_s),
        probe_timeout_s=_env_float("ROOMBA_PROBE_TIMEOUT_S", transport_cfg.probe_timeout_s),
        read_timeout_s=_env_float("ROOMBA_READ_TIMEOUT_S", transport_cfg.read_timeout_s),
        connect_timeout_s=_env_float("ROOMBA_CONNECT_TIMEOUT_S", transport_cfg.connect_timeout_s),
    )
    if env_http_host := _env_str("ROOMBA_HTTP_HOST"):
        http_cfg = replace(http_cfg, host=env_http_host)
    if _env_str("ROOMBA_HTTP_PORT"):
        http_cfg = replace(http_cfg, port=_env_int("ROOMBA_HTTP_PORT", http_cfg.port))
    if env_level := _env_str("ROOMBA_LOG_LEVEL"):
        logging_cfg = replace(logging_cfg, level=env_level)
    if env_log_file := _env_str("ROOMBA_LOG_FILE"):
        logging_cfg = replace(logging_cfg, file=env_log_file)

    if host is not None:
        robot_cfg = replace(robot_cfg, host=host)
    if port is not None:
        robot_cfg = replace(robot_cfg, port=port)
    if blid is not None:
        robot_cfg = replace(robot_cfg, blid=blid)
    if http_host is not None:
        http_cfg = replace(http_cfg, host=http_host)
    if http_port is not None:
        http_cfg = replace(http_cfg, port=http_port)

    if not robot_cfg.blid:
        robot_cfg = replace(robot_cfg, blid=_peek_blid(directory))

    if transport_cfg.mode not in TRANSPORT_MODES:
        raise ConfigError(
            f"transport.mode must be one of {', '.join(TRANSPORT_MODES)}",
            detail={"value": transport_cfg.mode},
        )
    if not 0 < http_cfg.port < 65536:
        raise ConfigError("http.port must be between 1 and 65535")
    if not 0 < robot_cfg.port < 65536:
        raise ConfigError("robot.port must be between 1 and 65535")
    if transport_cfg.probe_interval_s <= 0:
        raise ConfigError("transport.probe_interval_s must be positive")

    return Config(
        robot=robot_cfg,
        transport=transport_cfg,
        http=http_cfg,
        logging=logging_cfg,
        directory=directory,
    )


def is_loopback(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1")


def ensure_private_dir(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.suppress(OSError):
        directory.chmod(0o700)


def write_private_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace ``path`` with 0600 bytes from the first write."""
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def write_private_json(path: Path, data: Any) -> None:
    write_private_bytes(path, (json.dumps(data, indent=1) + "\n").encode("utf-8"))


def write_private_text(path: Path, text: str) -> None:
    write_private_bytes(path, text.encode("utf-8"))
