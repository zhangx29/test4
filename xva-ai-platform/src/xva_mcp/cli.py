"""Command line for the eight XVA tools: for people, scripts, and Copilot agent mode running it in the terminal
when MCP servers aren't allowed.

Two ways to run:
  - Directly on a store file, development only (XVA_ENV=dev; sample or dev data):
        set XVA_ENV=dev
        xva-cli --store sample_data\\xva_store.duckdb summary --as-of 2026-11-19 --level book
  - Through the hosted REST API, with the signed-in user's token (production):
        set XVA_API_TOKEN=<access token>
        xva-cli --api https://xva-api.apps.internal.example summary --level book

Either way the same entitlement checks run and an audit line is written (via=cli locally, via=rest through the API).

Exit codes: 0 ok, 2 invalid input or setup, 3 refused by entitlements, 4 no data, 5 API unreachable or failed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, ValidationError

from . import audit, tools
from .render import render

# Short command -> tool. The full tool names work as commands too.
COMMANDS: dict[str, str] = {
    "status": "get_data_status",
    "summary": "get_xva_summary",
    "movers": "get_xva_movers",
    "timeseries": "get_counterparty_xva_timeseries",
    "sens": "get_xva_sensitivities",
    "history": "get_xva_history",
    "pnl": "get_pnl_attribution",
    "search": "search_counterparties",
    "variants": "compare_counterparty_variants",
    "define": "get_definition",
}


class SetupError(Exception):
    """Wrong mode or missing configuration (exit 2)."""


class ApiError(Exception):
    """The REST API could not be reached or failed (exit 5)."""


# ---------------------------------------------------------------- argument parsing


def _option(field: str) -> str:
    return "--" + field.replace("_", "-")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xva-cli",
        description="Official XVA figures from the curated store. Read-only. Every answer ends with its source line.",
        epilog="Run 'xva-cli tools' for the list of commands, or 'xva-cli <command> -h' for its options.",
    )
    p.add_argument("--format", choices=["text", "json"], default="text", help="text (default) or json")
    p.add_argument("--api", default=os.environ.get("XVA_API_URL"), help="REST API base URL (or set XVA_API_URL)")
    p.add_argument("--store", help="store file to read directly (development only; or set XVA_STORE_PATH)")
    p.add_argument("--role", choices=["xva-desk", "xva-exec", "xva-admin"], help="development only: act as this role")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("tools", help="list the commands")

    for short, name in COMMANDS.items():
        fn = tools.TOOL_BY_NAME[name]
        sp = sub.add_parser(short, aliases=[name], help=tools.description(fn).split(". ")[0],
                            description=tools.description(fn))
        schema = tools.request_model(name).model_json_schema()
        required = set(schema.get("required", []))
        for field, prop in schema.get("properties", {}).items():
            choices = prop.get("enum")
            help_text = prop.get("description", "")
            if "default" in prop and prop["default"] is not None:
                help_text += f" (default {prop['default']})"
            sp.add_argument(
                _option(field), dest=field, required=field in required, choices=choices, help=help_text,
                metavar=field.upper() if not choices else None,
            )
    return p


def tools_listing() -> str:
    lines = ["Commands (each also accepts its full tool name):"]
    for short, name in COMMANDS.items():
        schema = tools.request_model(name).model_json_schema()
        required = set(schema.get("required", []))
        opts = " ".join(
            f"{_option(f)} X" if f in required else f"[{_option(f)} X]" for f in schema.get("properties", {})
        )
        first = tools.description(tools.TOOL_BY_NAME[name]).split(". ")[0].rstrip(".")
        lines.append(f"  {short:<11} {opts}")
        lines.append(f"              {first}.")
    return "\n".join(lines)


# ---------------------------------------------------------------- calling the tools


def call_local(name: str, raw: dict[str, Any]) -> BaseModel:
    if os.environ.get("XVA_ENV") != "dev":
        raise SetupError(
            "Reading a store file directly is for development only (set XVA_ENV=dev and use sample or dev data). "
            "Otherwise use --api <REST API URL> with your access token in XVA_API_TOKEN."
        )
    token = audit.channel.set("cli")
    try:
        return tools.call(name, raw)
    finally:
        audit.channel.reset(token)


def http_get(url: str, token: str | None, timeout: float = 30.0) -> tuple[int, Any]:
    """GET a JSON URL. Returns (status, parsed body). Replaced in tests."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"error": "http_error", "message": body[:300]}
    except urllib.error.URLError as exc:
        raise ApiError(f"Can't reach the XVA API at {url.split('?')[0]}: {exc.reason}") from exc


def call_api(base_url: str, name: str, raw: dict[str, Any]) -> BaseModel:
    query = urllib.parse.urlencode({k: str(v) for k, v in raw.items()})
    url = f"{base_url.rstrip('/')}{tools.API_PREFIX}/{name}" + (f"?{query}" if query else "")
    status, body = http_get(url, os.environ.get("XVA_API_TOKEN"))
    message = body.get("message", "") if isinstance(body, dict) else str(body)
    if status == 200:
        return tools.result_model(name).model_validate(body)
    if status == 401:
        raise SetupError("Sign-in failed or expired. Put a fresh access token in XVA_API_TOKEN (see README).")
    if status == 403 and isinstance(body, dict) and body.get("error") == "access_denied":
        raise tools.ToolDenied(message)
    if status == 404 and isinstance(body, dict) and body.get("error") == "not_found":
        raise tools.DataMissing(message)
    if status == 422:
        raise SetupError(f"Invalid input: {message}")
    raise ApiError(f"The XVA API returned HTTP {status}: {message}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "tools":
        print(tools_listing())
        return 0

    name = COMMANDS.get(args.command, args.command)
    fields = tools.request_model(name).model_fields
    raw = {f: getattr(args, f) for f in fields if getattr(args, f, None) is not None}

    try:
        if args.api:
            if args.store or args.role:
                raise SetupError("--store and --role are for development only and can't be used with --api.")
            result = call_api(args.api, name, raw)
        else:
            if args.store:
                os.environ["XVA_STORE_PATH"] = args.store
            if args.role:
                if os.environ.get("XVA_ENV") != "dev":
                    raise SetupError("--role works only in development (XVA_ENV=dev).")
                os.environ["XVA_DEV_ROLE"] = args.role
            result = call_local(name, raw)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
        print(f"Invalid input: {details}", file=sys.stderr)
        return 2
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except tools.ToolDenied as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 3
    except tools.DataMissing as exc:
        print(f"No data: {exc}", file=sys.stderr)
        return 4
    except ApiError as exc:
        print(str(exc), file=sys.stderr)
        return 5

    print(result.model_dump_json(indent=2) if args.format == "json" else render(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
