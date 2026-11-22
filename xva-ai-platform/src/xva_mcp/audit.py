"""One JSON line per tool call. Ship the file to the SIEM with the bank's standard log forwarder."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

_log = logging.getLogger("xva_mcp.audit")


def _path() -> str:
    return os.environ.get("XVA_AUDIT_LOG", "xva_audit.jsonl")


# Friendly names for front ends in the audit log; extend with XVA_CLIENT_NAMES (JSON: client ID -> name),
# e.g. the VS Code client ID and the Copilot Studio connector's app ID as seen in real tokens.
_DEFAULT_CLIENT_NAMES = {"ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b": "m365-copilot", "dev": "dev"}


def client_name(client_id: str | None) -> str | None:
    if not client_id:
        return None
    raw = os.environ.get("XVA_CLIENT_NAMES")
    names = {**_DEFAULT_CLIENT_NAMES, **(json.loads(raw) if raw else {})}
    return names.get(client_id, client_id)


def write(record: dict[str, Any]) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    line = json.dumps(record, default=str, sort_keys=True)
    with open(_path(), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    _log.info(line)


@contextmanager
def tool_call(
    user_id: str, role: str, tool: str, args: dict[str, Any], client: str | None = None
) -> Iterator[dict[str, Any]]:
    """Time a tool call and record its outcome (including denials and errors) and the calling front end."""
    started = time.perf_counter()
    extra: dict[str, Any] = {}
    outcome = "ok"
    try:
        yield extra
    except PermissionError as exc:
        outcome = f"denied: {exc}"
        raise
    except Exception as exc:
        outcome = f"error: {type(exc).__name__}"
        raise
    finally:
        write(
            {
                "user": user_id,
                "role": role,
                "client": client_name(client),
                "tool": tool,
                "args": args,
                "outcome": outcome,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                **extra,
            }
        )
