"""The eight XVA tools, independent of how they are called.

The same functions sit behind every front door, so entitlements, audit and results are identical:
  - server.py  MCP (VS Code / GitHub Copilot, Microsoft 365 Copilot MCP plugins)
  - rest.py    REST API with an OpenAPI description (M365 API plugins, Copilot Studio, the bank's AI platform)
  - cli.py     command line (people, scripts, or Copilot agent mode running it in the terminal)

Failures raise ToolDenied (entitlements) or DataMissing (no data for the request); each front door
turns them into its own error form. Messages are written for the agent and the user to read.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Annotated, Any, Callable, Literal, TypeVar, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, create_model

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


API_PREFIX = "/api/v1"  # REST paths: /api/v1/<tool name>


class ToolFailed(Exception):
    """A tool could not answer. The message is safe to show to the user and the agent."""


class ToolDenied(ToolFailed, PermissionError):
    """The caller's role does not allow this request."""


class DataMissing(ToolFailed, LookupError):
    """The store holds no data for this request."""


T = TypeVar("T")

INSTRUCTIONS = (
    "Official XVA figures for the XVA desk. Every result includes provenance (source, as-of date, run IDs, "
    "unit, sign convention): cite it. Call get_data_status before reporting figures for a date. "
    "Do not compute XVA yourself; use get_xva_movers and get_pnl_attribution for changes."
)
TopN = Annotated[int, Field(ge=1, le=200, description="Maximum rows to return (1-200)")]

# Argument descriptions. They reach every front door: MCP tool schemas, the OpenAPI description
# (Microsoft recommends one for each parameter of an API plugin) and the command-line help.
AsOf = Annotated[
    date | None,
    Field(description="Business date, YYYY-MM-DD. Omit for the latest date on which every source passed its checks."),
]
Start = Annotated[date, Field(description="Earlier business date, YYYY-MM-DD")]
End = Annotated[date, Field(description="Later business date, YYYY-MM-DD")]
LevelArg = Annotated[Level, Field(description="Grouping: desk, book or counterparty")]
CounterpartyId = Annotated[
    str, Field(min_length=2, max_length=40, description="Counterparty ID, e.g. CP001; find it with search_counterparties")
]
OptionalCounterpartyId = Annotated[
    str | None, Field(max_length=40, description="Limit to one counterparty ID (optional)")
]
AssetClassArg = Annotated[AssetClass, Field(description="Asset class: IR, FX, EQ, CO or CR")]
SensMeasureArg = Annotated[SensMeasure, Field(description="delta (IR, FX, EQ, CO), vega (IR) or cs01 (CR)")]
SensGroup = Annotated[
    Literal["counterparty", "risk_factor", "bucket"],
    Field(description="Group by counterparty, risk_factor, or bucket (risk factor + tenor)"),
]
ScopeKey = Annotated[
    str | None,
    Field(max_length=80, description="Desk name, book name or counterparty ID matching level; omit for the whole desk"),
]
Query = Annotated[str, Field(min_length=2, max_length=80, description="Counterparty ID or part of its name")]
Limit = Annotated[int, Field(ge=1, le=50, description="Maximum matches to return (1-50)")]
Term = Annotated[
    str, Field(min_length=2, max_length=60, description="Term to define, e.g. CVA, IR, market_CR, sign_convention")
]


def _default_date(as_of: date | None) -> date:
    if as_of is not None:
        return as_of
    clean = store.latest_clean_date()
    if clean is not None:
        return clean
    dates = store.business_dates()
    if not dates:
        raise DataMissing("The curated store holds no data yet.")
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
            {
                "user": "unknown",
                "role": None,
                "client": None,
                "via": audit.channel.get(),
                "tool": tool,
                "args": args,
                "outcome": f"denied: {exc}",
            }
        )
        raise ToolDenied(str(exc)) from exc
    try:
        with audit.tool_call(user.user_id, user.role, tool, args, client=user.client_id) as extra:
            entitlements.require_tool(user, tool)
            if level is not None or counterparty_id is not None:
                entitlements.require_level(user, level or "desk", counterparty_id)
            result = fn()
            extra["rows"] = _row_count(result)
            return result
    except entitlements.AccessDenied as exc:
        raise ToolDenied(str(exc)) from exc
    except store.DataNotAvailable as exc:
        raise DataMissing(str(exc)) from exc


# ------------------------------------------------------------------------- tools


def get_data_status(as_of: AsOf = None) -> DataStatus:
    """Load status of every source (XVA system, MFL spreadsheet, stored procedures) for a business date,
    plus the previous business date, the month-to-date base date and the latest fully clean date.
    Call this first. Defaults to the latest fully clean date."""
    d = _default_date(as_of)
    return _guarded("get_data_status", {"as_of": d}, lambda: store.data_status(d))


def get_xva_summary(as_of: AsOf = None, level: LevelArg = "desk", top_n: TopN = 20) -> XvaSummary:
    """Top-of-house CVA, DVA, FVA and total for one business date, grouped by desk, book or counterparty,
    sorted by absolute total. 'totals' covers all groups even when rows are truncated."""
    d = _default_date(as_of)
    return _guarded(
        "get_xva_summary",
        {"as_of": d, "level": level, "top_n": top_n},
        lambda: store.xva_summary(d, level, top_n),
        level=level,
    )


def get_xva_movers(start: Start, end: End, level: LevelArg = "counterparty", top_n: TopN = 10) -> XvaMovers:
    """Change in CVA, DVA, FVA and total between two business dates by desk, book or counterparty,
    sorted by absolute change. Use this instead of subtracting numbers yourself."""
    return _guarded(
        "get_xva_movers",
        {"start": start, "end": end, "level": level, "top_n": top_n},
        lambda: store.xva_movers(start, end, level, top_n),
        level=level,
    )


def get_counterparty_xva_timeseries(
    counterparty_id: CounterpartyId, start: Start, end: End
) -> CounterpartyTimeseries:
    """Daily CVA, DVA, FVA and total for one counterparty between two dates (inclusive).
    Resolve names to IDs with search_counterparties first."""
    return _guarded(
        "get_counterparty_xva_timeseries",
        {"counterparty_id": counterparty_id, "start": start, "end": end},
        lambda: store.counterparty_timeseries(counterparty_id, start, end),
        counterparty_id=counterparty_id,
    )


def get_xva_sensitivities(
    asset_class: AssetClassArg,
    measure: SensMeasureArg = "delta",
    group_by: SensGroup = "risk_factor",
    as_of: AsOf = None,
    counterparty_id: OptionalCounterpartyId = None,
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
    start: Start,
    end: End,
    level: LevelArg = "desk",
    key: ScopeKey = None,
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
    query: Query,
    limit: Limit = 10,
) -> CounterpartySearch:
    """Find counterparties by ID or part of the name. Returns IDs, rating, sector, region and book.
    If several match, ask the user which one they mean."""
    return _guarded(
        "search_counterparties",
        {"query": query, "limit": limit},
        lambda: store.search_counterparties(query, limit),
        level="counterparty",
    )


def get_definition(term: Term) -> DefinitionResult:
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


TOOL_BY_NAME: dict[str, Callable[..., Any]] = {fn.__name__: fn for fn in TOOLS}


def description(fn: Callable[..., Any]) -> str:
    return " ".join((fn.__doc__ or "").split())


@lru_cache(maxsize=None)
def request_model(name: str) -> type[BaseModel]:
    """Pydantic model of a tool's arguments, built from its signature (types, defaults, limits).
    The REST API and the CLI validate with it; FastMCP builds the same from the signature itself."""
    import inspect

    fn = TOOL_BY_NAME[name]
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, Any] = {}
    for param in inspect.signature(fn).parameters.values():
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (hints[param.name], default)
    return create_model(
        f"{name}_args", __config__=ConfigDict(extra="forbid"), **fields  # type: ignore[call-overload]
    )


@lru_cache(maxsize=None)
def result_model(name: str) -> type[BaseModel]:
    return get_type_hints(TOOL_BY_NAME[name])["return"]


def validate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Check and convert raw arguments (e.g. strings from a URL or command line). Raises
    pydantic.ValidationError on bad input and KeyError for an unknown tool."""
    model = request_model(name)
    return dict(model.model_validate(args))


def call(name: str, args: dict[str, Any]) -> BaseModel:
    """Validate the arguments and run the tool as the current caller."""
    return TOOL_BY_NAME[name](**validate_args(name, args))
