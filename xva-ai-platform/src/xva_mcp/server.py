"""XVA data MCP server: read-only, typed tools with entitlement checks and an audit line per call.

Run locally for development (sample data, stdio):
    XVA_ENV=dev XVA_STORE_PATH=sample_data/xva_store.sqlite python -m xva_mcp.server

The store is SQLite by default (no extra package) or DuckDB; see db.py for how the backend is chosen.

Run in production (Streamable HTTP behind TLS, tokens from the bank's identity provider):
    XVA_OAUTH_ISSUER=... XVA_OAUTH_AUDIENCE=... XVA_OAUTH_JWKS_URL=... XVA_RESOURCE_URL=... \
    python -m xva_mcp.server --transport streamable-http --host 0.0.0.0 --port 8080

XVA_OAUTH_AUDIENCE and XVA_ALLOWED_CLIENT_IDS take comma-separated lists, so one server can serve
VS Code / GitHub Copilot, Microsoft 365 Copilot and Copilot Studio at the same time (see auth.py).
"""

from __future__ import annotations

import argparse
import os
from datetime import date
from typing import Annotated, Any, Callable, Literal, TypeVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import audit, entitlements, store
from .models import (
    AssetClass,
    CounterpartySearch,
    CounterpartyTimeseries,
    DataStatus,
    DefinitionResult,
    Level,
    PnlAttribution,
    SensitivityReport,
    SensMeasure,
    XvaMovers,
    XvaSummary,
)

T = TypeVar("T")

INSTRUCTIONS = (
    "Official XVA figures for the XVA desk. Every result includes provenance (source, as-of date, run IDs, "
    "unit, sign convention): cite it. Call get_data_status before reporting figures for a date. "
    "Do not compute XVA yourself; use get_xva_movers and get_pnl_attribution for changes."
)
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
TopN = Annotated[int, Field(ge=1, le=200, description="Maximum rows to return (1-200)")]


def _default_date(as_of: date | None) -> date:
    if as_of is not None:
        return as_of
    clean = store.latest_clean_date()
    if clean is not None:
        return clean
    dates = store.business_dates()
    if not dates:
        raise ToolError("The curated store holds no data yet.")
    return dates[-1]


def _row_count(result: Any) -> int | None:
    for attr in ("rows", "points", "matches", "drivers", "sources"):
        if hasattr(result, attr):
            return len(getattr(result, attr))
    return None


def _guarded(
    tool: str,
    args: dict[str, Any],
    fn: Callable[[], T],
    *,
    level: str | None = None,
    counterparty_id: str | None = None,
) -> T:
    """Resolve the caller, check entitlements, run the query, and audit the outcome."""
    try:
        user = entitlements.current_user()
    except entitlements.AccessDenied as exc:
        audit.write(
            {"user": "unknown", "role": None, "client": None, "tool": tool, "args": args, "outcome": f"denied: {exc}"}
        )
        raise ToolError(str(exc)) from exc
    try:
        with audit.tool_call(user.user_id, user.role, tool, args, client=user.client_id) as extra:
            entitlements.require_tool(user, tool)
            if level is not None or counterparty_id is not None:
                entitlements.require_level(user, level or "desk", counterparty_id)
            result = fn()
            extra["rows"] = _row_count(result)
            return result
    except (entitlements.AccessDenied, store.DataNotAvailable) as exc:
        raise ToolError(str(exc)) from exc


# ------------------------------------------------------------------------- tools


def get_data_status(as_of: date | None = None) -> DataStatus:
    """Load status of every source (XVA system, MFL spreadsheet, stored procedures) for a business date,
    plus the previous business date, the month-to-date base date and the latest fully clean date.
    Call this first. Defaults to the latest fully clean date."""
    d = _default_date(as_of)
    return _guarded("get_data_status", {"as_of": d}, lambda: store.data_status(d))


def get_xva_summary(as_of: date | None = None, level: Level = "desk", top_n: TopN = 20) -> XvaSummary:
    """Top-of-house CVA, DVA, FVA and total for one business date, grouped by desk, book or counterparty,
    sorted by absolute total. 'totals' covers all groups even when rows are truncated."""
    d = _default_date(as_of)
    return _guarded(
        "get_xva_summary",
        {"as_of": d, "level": level, "top_n": top_n},
        lambda: store.xva_summary(d, level, top_n),
        level=level,
    )


def get_xva_movers(start: date, end: date, level: Level = "counterparty", top_n: TopN = 10) -> XvaMovers:
    """Change in CVA, DVA, FVA and total between two business dates by desk, book or counterparty,
    sorted by absolute change. Use this instead of subtracting numbers yourself."""
    return _guarded(
        "get_xva_movers",
        {"start": start, "end": end, "level": level, "top_n": top_n},
        lambda: store.xva_movers(start, end, level, top_n),
        level=level,
    )


def get_counterparty_xva_timeseries(counterparty_id: str, start: date, end: date) -> CounterpartyTimeseries:
    """Daily CVA, DVA, FVA and total for one counterparty between two dates (inclusive).
    Resolve names to IDs with search_counterparties first."""
    return _guarded(
        "get_counterparty_xva_timeseries",
        {"counterparty_id": counterparty_id, "start": start, "end": end},
        lambda: store.counterparty_timeseries(counterparty_id, start, end),
        counterparty_id=counterparty_id,
    )


def get_xva_sensitivities(
    asset_class: AssetClass,
    measure: SensMeasure = "delta",
    group_by: Literal["counterparty", "risk_factor", "bucket"] = "risk_factor",
    as_of: date | None = None,
    counterparty_id: str | None = None,
    top_n: TopN = 20,
) -> SensitivityReport:
    """XVA sensitivities for one asset class (IR, FX, EQ, CO, CR). Measures: delta (IR, FX, EQ, CO),
    vega (IR), cs01 (CR). Group by counterparty, risk factor or risk factor + tenor bucket.
    The unit is in provenance.unit."""
    d = _default_date(as_of)
    level = "counterparty" if group_by == "counterparty" else "desk"
    return _guarded(
        "get_xva_sensitivities",
        {
            "as_of": d,
            "asset_class": asset_class,
            "measure": measure,
            "group_by": group_by,
            "counterparty_id": counterparty_id,
            "top_n": top_n,
        },
        lambda: store.sensitivities(d, asset_class, measure, group_by, counterparty_id, top_n),
        level=level,
        counterparty_id=counterparty_id,
    )


def get_pnl_attribution(
    start: date,
    end: date,
    level: Level = "desk",
    key: str | None = None,
) -> PnlAttribution:
    """XVA P&L explain between two business dates (start exclusive, end inclusive) by driver: market moves
    per asset class, time decay, new trades, amendments, terminations, model/parameter changes, unexplained.
    level + key filter the scope (desk name, book name or counterparty_id); omit key for the whole desk.
    unexplained_flag and reconciliation_gap tell you whether the explain can be relied on."""
    return _guarded(
        "get_pnl_attribution",
        {"start": start, "end": end, "level": level, "key": key},
        lambda: store.pnl_attribution(start, end, level, key),
        level=level if key else "desk",
        counterparty_id=key if level == "counterparty" else None,
    )


def search_counterparties(
    query: Annotated[str, Field(min_length=2, max_length=80)],
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> CounterpartySearch:
    """Find counterparties by ID or part of the name. Returns IDs, rating, sector, region and book.
    If several match, ask the user which one they mean."""
    return _guarded(
        "search_counterparties",
        {"query": query, "limit": limit},
        lambda: store.search_counterparties(query, limit),
        level="counterparty",
    )


def get_definition(term: Annotated[str, Field(min_length=2, max_length=60)]) -> DefinitionResult:
    """Desk definition, unit and convention for a measure (CVA, DVA, FVA), sensitivity asset class,
    P&L driver, 'sign_convention', 'reporting_currency' or 'thresholds'."""
    return _guarded("get_definition", {"term": term}, lambda: store.definition(term))


TOOLS: list[Callable[..., Any]] = [
    get_data_status,
    get_xva_summary,
    get_xva_movers,
    get_counterparty_xva_timeseries,
    get_xva_sensitivities,
    get_pnl_attribution,
    search_counterparties,
    get_definition,
]


def build_server(*, auth: bool, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    kwargs: dict[str, Any] = {"instructions": INSTRUCTIONS, "host": host, "port": port, "stateless_http": True}
    if auth:
        from mcp.server.auth.settings import AuthSettings

        from .auth import JwtTokenVerifier, split_list

        env = os.environ
        issuers = split_list(env["XVA_OAUTH_ISSUER"])  # comma-separated; the first is advertised to clients
        kwargs["token_verifier"] = JwtTokenVerifier(
            issuers=issuers,
            audiences=env["XVA_OAUTH_AUDIENCE"],  # comma-separated: one per front end
            jwks_url=env["XVA_OAUTH_JWKS_URL"],
            allowed_client_ids=env.get("XVA_ALLOWED_CLIENT_IDS"),  # optional, comma-separated
        )
        kwargs["auth"] = AuthSettings(
            issuer_url=issuers[0],
            resource_server_url=env["XVA_RESOURCE_URL"],
            required_scopes=env.get("XVA_REQUIRED_SCOPES", "xva.read").split(),
            # Entra audiences are app IDs or Application ID URIs, not this server's URL, so the
            # JwtTokenVerifier checks the audience list itself instead of the SDK's URL comparison.
            validate_token_resource=False,
        )
    server = FastMCP("xva-data", **kwargs)
    for fn in TOOLS:
        server.tool(annotations=READ_ONLY)(fn)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="XVA data MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    dev = os.environ.get("XVA_ENV") == "dev"
    if args.transport == "stdio" and not dev:
        raise SystemExit("stdio transport is for development only: set XVA_ENV=dev and use sample data.")
    use_auth = args.transport == "streamable-http" and not dev
    if args.transport == "streamable-http" and dev and args.host != "127.0.0.1":
        raise SystemExit("Unauthenticated dev HTTP must bind to 127.0.0.1.")
    build_server(auth=use_auth, host=args.host, port=args.port).run(transport=args.transport)


if __name__ == "__main__":
    main()
