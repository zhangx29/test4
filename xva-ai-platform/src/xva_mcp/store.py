"""Read-only access to the curated XVA store (SQLite or DuckDB through db.py; the bank's SQL platform later).

Every query is parameterised. Column and grouping expressions come from fixed dictionaries
keyed by validated enums, never from caller text. The SQL sticks to what SQLite and DuckDB share.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from . import db

from .models import (
    CounterpartyInfo,
    CounterpartySearch,
    CounterpartyTimeseries,
    DataStatus,
    DefinitionResult,
    DriverRow,
    MoverRow,
    PnlAttribution,
    Provenance,
    SensitivityReport,
    SensRow,
    SourceStatus,
    XvaMovers,
    XvaPoint,
    XvaRow,
    XvaSummary,
)

SOURCE_LABEL = "XVA system official EOD run (curated store)"

_GROUP = {
    "desk": ("c.desk", "c.desk"),
    "book": ("c.book", "c.book"),
    "counterparty": ("c.counterparty_id", "c.name"),
}
_SENS_GROUP = {
    "counterparty": ("c.counterparty_id", "c.name"),
    "risk_factor": ("s.risk_factor", "s.risk_factor"),
    "bucket": ("s.risk_factor || ' ' || s.bucket", "s.risk_factor || ' ' || s.bucket"),
}
_FILTER = {"desk": "c.desk", "book": "c.book", "counterparty": "c.counterparty_id"}


class DataNotAvailable(LookupError):
    """Raised when the requested slice is not in the store. The message is shown to the agent."""


@lru_cache(maxsize=1)
def dictionary() -> dict[str, Any]:
    path = os.environ.get("XVA_DICTIONARY_PATH")
    if path:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    return yaml.safe_load(resources.files("xva_mcp").joinpath("dictionary.yaml").read_text(encoding="utf-8"))


def _store_path() -> str:
    path = os.environ.get("XVA_STORE_PATH", db.DEFAULT_STORE_PATH)
    if not os.path.exists(path):
        raise DataNotAvailable(f"Curated store not found at {path}.")
    return path


def _rows(sql: str, params: list[Any] | None = None) -> list[tuple]:
    with db.connect(_store_path(), read_only=True) as con:
        return con.execute(sql, params or []).fetchall()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(ts: datetime | None) -> datetime | None:
    """Report load times in UTC whatever the backend or the server's time zone."""
    if ts is None:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


# ---------------------------------------------------------------- calendar and status


def business_dates() -> list[date]:
    return [r[0] for r in _rows("SELECT DISTINCT as_of FROM xva_value ORDER BY as_of")]


def _require_date(d: date) -> None:
    dates = business_dates()
    if d not in dates:
        latest = dates[-1] if dates else None
        raise DataNotAvailable(f"No XVA data for {d}. Latest available date: {latest}.")


def previous_business_date(d: date) -> date | None:
    earlier = [x for x in business_dates() if x < d]
    return earlier[-1] if earlier else None


def month_start_reference(d: date) -> date | None:
    first_of_month = d.replace(day=1)
    earlier = [x for x in business_dates() if x < first_of_month]
    return earlier[-1] if earlier else None


def latest_clean_date(on_or_before: date | None = None) -> date | None:
    expected = dictionary()["expected_sources"]
    sql = f"""
        SELECT as_of FROM data_status
        WHERE source IN ({db.placeholders(len(expected))}) AND checks_passed = TRUE
        GROUP BY as_of HAVING COUNT(DISTINCT source) = ?
        ORDER BY as_of
    """
    clean = [r[0] for r in _rows(sql, [*expected, len(expected)])]
    if on_or_before is not None:
        clean = [x for x in clean if x <= on_or_before]
    return clean[-1] if clean else None


def data_status(as_of: date) -> DataStatus:
    expected = dictionary()["expected_sources"]
    rows = _rows(
        "SELECT source, run_id, loaded_at, checks_passed, notes FROM data_status WHERE as_of = ? ORDER BY source",
        [as_of],
    )
    sources = [
        SourceStatus(source=r[0], run_id=r[1], loaded_at=_utc(r[2]), checks_passed=bool(r[3]), notes=r[4]) for r in rows
    ]
    present = {s.source for s in sources}
    missing = [s for s in expected if s not in present]
    return DataStatus(
        as_of=as_of,
        all_passed=not missing and all(s.checks_passed for s in sources),
        sources=sources,
        missing_sources=missing,
        previous_business_date=previous_business_date(as_of),
        month_start_reference=month_start_reference(as_of),
        latest_clean_date=latest_clean_date(),
    )


def _run_ids(dates: list[date]) -> list[str]:
    rows = _rows(
        "SELECT DISTINCT run_id FROM data_status WHERE source = 'xva_system' "
        f"AND as_of IN ({db.placeholders(len(dates))}) ORDER BY run_id",
        list(dates),
    )
    return [r[0] for r in rows]


def _provenance(dates: list[date], unit: str | None = None) -> Provenance:
    d = dictionary()
    return Provenance(
        source=SOURCE_LABEL,
        as_of=max(dates),
        run_ids=_run_ids(dates),
        unit=unit or d["reporting_currency"],
        sign_convention=d["sign_convention"],
        generated_at=_now(),
    )


# ---------------------------------------------------------------- XVA levels


def _summary_rows(as_of: date, level: str) -> list[XvaRow]:
    key_expr, label_expr = _GROUP[level]
    sql = f"""
        SELECT {key_expr} AS key, {label_expr} AS label,
               SUM(CASE WHEN v.measure = 'CVA' THEN v.value ELSE 0 END) AS cva,
               SUM(CASE WHEN v.measure = 'DVA' THEN v.value ELSE 0 END) AS dva,
               SUM(CASE WHEN v.measure = 'FVA' THEN v.value ELSE 0 END) AS fva
        FROM xva_value v JOIN dim_counterparty c ON c.counterparty_id = v.counterparty_id
        WHERE v.as_of = ?
        GROUP BY 1, 2
    """
    out = []
    for key, label, cva, dva, fva in _rows(sql, [as_of]):
        out.append(XvaRow(key=key, label=label, cva=cva, dva=dva, fva=fva, total=cva + dva + fva))
    return sorted(out, key=lambda r: abs(r.total), reverse=True)


def _total_row(rows: list[XvaRow], label: str = "Total") -> XvaRow:
    cva, dva, fva = (sum(getattr(r, m) for r in rows) for m in ("cva", "dva", "fva"))
    return XvaRow(key="TOTAL", label=label, cva=cva, dva=dva, fva=fva, total=cva + dva + fva)


def xva_summary(as_of: date, level: str, top_n: int) -> XvaSummary:
    _require_date(as_of)
    rows = _summary_rows(as_of, level)
    return XvaSummary(
        as_of=as_of,
        level=level,  # type: ignore[arg-type]
        rows=rows[:top_n],
        totals=_total_row(rows),
        truncated=len(rows) > top_n,
        provenance=_provenance([as_of]),
    )


def xva_movers(start: date, end: date, level: str, top_n: int) -> XvaMovers:
    if start >= end:
        raise DataNotAvailable("start must be earlier than end.")
    _require_date(start)
    _require_date(end)
    before = {r.key: r for r in _summary_rows(start, level)}
    after = {r.key: r for r in _summary_rows(end, level)}
    zero = XvaRow(key="", label="", cva=0.0, dva=0.0, fva=0.0, total=0.0)
    movers = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key, zero), after.get(key, zero)
        movers.append(
            MoverRow(
                key=key,
                label=(a.label or b.label),
                total_start=b.total,
                total_end=a.total,
                change=a.total - b.total,
                cva_change=a.cva - b.cva,
                dva_change=a.dva - b.dva,
                fva_change=a.fva - b.fva,
            )
        )
    movers.sort(key=lambda m: abs(m.change), reverse=True)
    return XvaMovers(
        start=start,
        end=end,
        level=level,  # type: ignore[arg-type]
        rows=movers[:top_n],
        total_change=sum(m.change for m in movers),
        truncated=len(movers) > top_n,
        provenance=_provenance([start, end]),
    )


def counterparty_timeseries(counterparty_id: str, start: date, end: date) -> CounterpartyTimeseries:
    names = _rows("SELECT name FROM dim_counterparty WHERE counterparty_id = ?", [counterparty_id])
    if not names:
        raise DataNotAvailable(f"Unknown counterparty_id {counterparty_id}. Use search_counterparties first.")
    sql = """
        SELECT as_of,
               SUM(CASE WHEN measure = 'CVA' THEN value ELSE 0 END),
               SUM(CASE WHEN measure = 'DVA' THEN value ELSE 0 END),
               SUM(CASE WHEN measure = 'FVA' THEN value ELSE 0 END)
        FROM xva_value WHERE counterparty_id = ? AND as_of BETWEEN ? AND ?
        GROUP BY as_of ORDER BY as_of
    """
    points = [
        XvaPoint(as_of=d, cva=c, dva=dv, fva=f, total=c + dv + f)
        for d, c, dv, f in _rows(sql, [counterparty_id, start, end])
    ]
    if not points:
        raise DataNotAvailable(f"No XVA data for {counterparty_id} between {start} and {end}.")
    return CounterpartyTimeseries(
        counterparty_id=counterparty_id,
        name=names[0][0],
        points=points,
        provenance=_provenance([p.as_of for p in points]),
    )


# ---------------------------------------------------------------- sensitivities


def sensitivities(
    as_of: date, asset_class: str, measure: str, group_by: str, counterparty_id: str | None, top_n: int
) -> SensitivityReport:
    _require_date(as_of)
    spec = dictionary()["sensitivities"].get(asset_class, {}).get(measure)
    if spec is None:
        raise DataNotAvailable(f"No {measure} sensitivity is defined for {asset_class}.")
    key_expr, label_expr = _SENS_GROUP[group_by]
    where = "s.as_of = ? AND s.asset_class = ? AND s.measure = ?"
    params: list[Any] = [as_of, asset_class, measure]
    if counterparty_id:
        where += " AND s.counterparty_id = ?"
        params.append(counterparty_id)
    sql = f"""
        SELECT {key_expr} AS key, {label_expr} AS label, SUM(s.value) AS value
        FROM xva_sensitivity s JOIN dim_counterparty c ON c.counterparty_id = s.counterparty_id
        WHERE {where}
        GROUP BY 1, 2
    """
    rows = sorted(
        (SensRow(key=k, label=lbl, value=v) for k, lbl, v in _rows(sql, params)),
        key=lambda r: abs(r.value),
        reverse=True,
    )
    return SensitivityReport(
        as_of=as_of,
        asset_class=asset_class,  # type: ignore[arg-type]
        measure=measure,  # type: ignore[arg-type]
        group_by=group_by,  # type: ignore[arg-type]
        counterparty_id=counterparty_id,
        rows=rows[:top_n],
        total=sum(r.value for r in rows),
        truncated=len(rows) > top_n,
        provenance=_provenance([as_of], unit=spec["unit"]),
    )


# ---------------------------------------------------------------- P&L attribution


def pnl_attribution(start: date, end: date, level: str, key: str | None) -> PnlAttribution:
    if start >= end:
        raise DataNotAvailable("start must be earlier than end.")
    _require_date(start)
    _require_date(end)
    if level == "counterparty" and not key:
        raise DataNotAvailable("Counterparty level needs a counterparty_id.")
    where, params = "", []
    if key:
        where = f" AND {_FILTER[level]} = ?"
        params = [key]

    values = _rows(
        f"""
        SELECT v.as_of, SUM(v.value)
        FROM xva_value v JOIN dim_counterparty c ON c.counterparty_id = v.counterparty_id
        WHERE v.as_of IN (?, ?){where}
        GROUP BY v.as_of
        """,
        [start, end, *params],
    )
    by_date = dict(values)
    if end not in by_date:
        raise DataNotAvailable(f"No XVA values for that selection on {end}.")
    xva_change = by_date.get(end, 0.0) - by_date.get(start, 0.0)

    drivers = _rows(
        f"""
        SELECT p.driver, SUM(p.value)
        FROM xva_pnl_explain p JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
        WHERE p.as_of > ? AND p.as_of <= ?{where}
        GROUP BY p.driver
        """,
        [start, end, *params],
    )
    order = list(dictionary()["pnl_drivers"])
    driver_rows = sorted(
        (DriverRow(driver=d, value=v) for d, v in drivers),
        key=lambda r: order.index(r.driver) if r.driver in order else len(order),
    )
    unexplained = sum(r.value for r in driver_rows if r.driver == "unexplained")
    t = dictionary()["thresholds"]
    flag = abs(unexplained) > t["unexplained_abs_cad"] or (
        xva_change != 0 and abs(unexplained) > t["unexplained_pct_of_change"] * abs(xva_change)
    )
    return PnlAttribution(
        start=start,
        end=end,
        level=level,  # type: ignore[arg-type]
        key=key,
        xva_change=xva_change,
        drivers=driver_rows,
        unexplained=unexplained,
        unexplained_flag=flag,
        reconciliation_gap=xva_change - sum(r.value for r in driver_rows),
        provenance=_provenance([start, end]),
    )


# ---------------------------------------------------------------- reference


def search_counterparties(query: str, limit: int) -> CounterpartySearch:
    rows = _rows(
        """
        SELECT counterparty_id, name, rating, sector, region, book FROM dim_counterparty
        WHERE counterparty_id = upper(?) OR lower(name) LIKE '%' || lower(?) || '%'
        ORDER BY name LIMIT ?
        """,
        [query, query, limit],
    )
    return CounterpartySearch(
        query=query,
        matches=[
            CounterpartyInfo(counterparty_id=r[0], name=r[1], rating=r[2], sector=r[3], region=r[4], book=r[5])
            for r in rows
        ],
    )


def definition(term: str) -> DefinitionResult:
    d = dictionary()
    t = term.strip()
    sections = {
        "measures": d.get("measures", {}),
        "sensitivities": d.get("sensitivities", {}),
        "pnl_drivers": d.get("pnl_drivers", {}),
    }
    for section, entries in sections.items():
        for name, entry in entries.items():
            if name.lower() == t.lower():
                return DefinitionResult(term=name, found=True, entry={"section": section, "value": entry})
    for name in ("sign_convention", "reporting_currency", "thresholds"):
        if name.lower() == t.lower().replace(" ", "_"):
            return DefinitionResult(term=name, found=True, entry={"section": "conventions", "value": d[name]})
    available = sorted({*sections["measures"], *sections["sensitivities"], *sections["pnl_drivers"]})
    return DefinitionResult(term=term, found=False, available_terms=available)
