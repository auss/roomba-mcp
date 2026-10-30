"""Logging setup.

Every command is one JSON line: ``{tool, payload, transport, result}``. Logs
go to stderr so stdio transports are never corrupted; an optional file gets a
copy.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

log = logging.getLogger("roomba_mcp.commands")


def configure(level: str = "info", file: str = "") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if file:
        handlers.append(logging.FileHandler(file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        handlers=handlers,
        force=True,
    )


def log_command(
    tool: str,
    payload: Any,
    *,
    transport: str,
    result: Any,
) -> None:
    log.info(
        json.dumps(
            {
                "event": "command",
                "tool": tool,
                "payload": payload,
                "transport": transport,
                "result": result,
            },
            default=str,
            sort_keys=True,
        )
    )
