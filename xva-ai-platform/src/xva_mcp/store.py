"""Read-only access to the curated XVA store (SQLite or DuckDB through db.py; the bank's SQL platform later).

Every query is parameterised. Column and grouping expressions come from fixed dictionaries
keyed by validated enums, never from caller text. The SQL sticks to what SQLite and DuckDB share.
"""

from __future__ import annotations

import os
import re
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
    VariantComparison,
    VariantPnlRow,
    VariantSensRow,
    VariantValueRow,
    XvaHistory,
    XvaMovers,
    XvaPoint,
    XvaRow,
    XvaSummary,
)

SOURCE_LABEL = "XVA system official EOD run (curated store)"

# level -> (key expression, label expression); "{t}" is the fact table's alias
_GROUP = {
    "desk": ("COALESCE(c.desk, 'Desk')", "COALESCE(c.desk, 'Desk')"),
    "entity": ("COALESCE({t}.legal_entity, 'n/a')", "COALESCE({t}.legal_entity, 'n/a')"),
    "book": ("COALESCE(c.book, 'Unassigned')", "COALESCE(c.book, 'Unassigned')"),
    "counterparty": ("c.counterparty_id", "COALESCE(c.name, c.counterparty_id)"),
}
_SENS_GROUP = {
    "counterparty": ("c.counterparty_id", "COALESCE(c.name, c.counterparty_id)"),
    "entity": ("COALESCE(s.legal_entity, 'n/a')", "COALESCE(s.legal_entity, 'n/a')"),
    "risk_factor": ("s.risk_factor", "s.risk_factor"),
    "bucket": (
        "s.risk_factor || ' ' || COALESCE(s.bucket, 'parallel')",
        "s.risk_factor || ' ' || COALESCE(s.bucket, 'parallel')",
    ),
}
_FILTER = {
    "desk": "COALESCE(c.desk, 'Desk')",
    "entity": "{t}.legal_entity",
    "book": "COALESCE(c.book, 'Unassigned')",
    "counterparty": "c.counterparty_id",
}


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


_FACT = re.compile(
    r"\bFROM\s+(xva_value|xva_sensitivity|xva_pnl_explain|xva_pnl_actual)\b(?:\s+(?:AS\s+)?(?!(?:WHERE|GROUP|ORDER|JOIN|LIMIT)\b)([a-z]\w*))?",
    re.IGNORECASE,
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _official_rows_only(sql: str) -> str:
    """Read fact tables only for each date's official run: the run recorded in data_status for the official
    source. A restated day is landed as a new run and replaces the official one; older runs stay in the
    tables for audit but are never added in."""
    source = official_source()
    if not _SAFE_NAME.match(source):  # comes from dictionary.yaml, still checked before it reaches SQL
        raise ValueError(f"official_source {source!r} in dictionary.yaml is not a plain name")

    def sub(m: re.Match[str]) -> str:
        table, alias = m.group(1), m.group(2) or m.group(1)
        return (
            f"FROM (SELECT t.* FROM {table} t WHERE EXISTS (SELECT 1 FROM data_status ds "
            f"WHERE ds.as_of = t.as_of AND ds.run_id = t.run_id AND ds.source = '{source}')) AS {alias}"
        )

    return _FACT.sub(sub, sql)


def _rows(sql: str, params: list[Any] | None = None) -> list[tuple]:
    with db.connect(_store_path(), read_only=True) as con:
        return con.execute(_official_rows_only(sql), params or []).fetchall()


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
        all_passed=not missing and all(s.checks_passed for s in sources if s.source in expected),
        sources=sources,
        missing_sources=missing,
        previous_business_date=previous_business_date(as_of),
        month_start_reference=month_start_reference(as_of),
        latest_clean_date=latest_clean_date(),
    )


def official_source() -> str:
    return dictionary().get("official_source", "xva_system")


def components() -> list[str]:
    """XVA components stored per counterparty, in dictionary order (measures with an in_total flag)."""
    return [m for m, spec in dictionary().get("measures", {}).items() if isinstance(spec, dict) and "in_total" in spec]


def total_components() -> list[str]:
    return [m for m in components() if dictionary()["measures"][m]["in_total"]]


def _total(values: dict[str, float]) -> float:
    return sum(values.get(m, 0.0) for m in total_components())


def _ordered(values: dict[str, float]) -> dict[str, float]:
    return {m: values.get(m, 0.0) for m in components()}


def _run_ids(dates: list[date]) -> list[str]:
    rows = _rows(
        "SELECT DISTINCT run_id FROM data_status WHERE source = ? "
        f"AND as_of IN ({db.placeholders(len(dates))}) ORDER BY run_id",
        [official_source(), *dates],
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
    key_expr, label_expr = (e.format(t="v") for e in _GROUP[level])
    sql = f"""
        SELECT {key_expr} AS key, {label_expr} AS label, v.measure, SUM(v.value)
        FROM xva_value v JOIN dim_counterparty c ON c.counterparty_id = v.counterparty_id
        WHERE v.as_of = ?
        GROUP BY 1, 2, 3
    """
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for key, label, measure, value in _rows(sql, [as_of]):
        grouped.setdefault((key, label), {})[measure] = value
    out = [
        XvaRow(key=key, label=label, components=_ordered(vals), total=_total(vals))
        for (key, label), vals in grouped.items()
    ]
    # ties (e.g. zero totals) in key order, so the answer doesn't depend on the database's row order
    return sorted(sorted(out, key=lambda r: r.key), key=lambda r: abs(r.total), reverse=True)


def _total_row(rows: list[XvaRow], label: str = "Total") -> XvaRow:
    sums = {m: sum(r.components.get(m, 0.0) for r in rows) for m in components()}
    return XvaRow(key="TOTAL", label=label, components=sums, total=_total(sums))


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
    zero = XvaRow(key="", label="", components=_ordered({}), total=0.0)
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
                component_changes={m: a.components.get(m, 0.0) - b.components.get(m, 0.0) for m in components()},
            )
        )
    movers.sort(key=lambda m: m.key)
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


def _points(rows: list[tuple]) -> list[XvaPoint]:
    by_date: dict[date, dict[str, float]] = {}
    for d, measure, value in rows:
        by_date.setdefault(d, {})[measure] = value
    return [XvaPoint(as_of=d, components=_ordered(v), total=_total(v)) for d, v in sorted(by_date.items())]


def counterparty_timeseries(counterparty_id: str, start: date, end: date) -> CounterpartyTimeseries:
    names = _rows("SELECT COALESCE(name, counterparty_id) FROM dim_counterparty WHERE counterparty_id = ?",
                  [counterparty_id])
    if not names:
        raise DataNotAvailable(f"Unknown counterparty_id {counterparty_id}. Use search_counterparties first.")
    points = _points(
        _rows(
            """
            SELECT as_of, measure, SUM(value) FROM xva_value
            WHERE counterparty_id = ? AND as_of BETWEEN ? AND ?
            GROUP BY as_of, measure ORDER BY as_of
            """,
            [counterparty_id, start, end],
        )
    )
    if not points:
        raise DataNotAvailable(f"No XVA data for {counterparty_id} between {start} and {end}.")
    return CounterpartyTimeseries(
        counterparty_id=counterparty_id,
        name=names[0][0],
        points=points,
        provenance=_provenance([p.as_of for p in points]),
    )


def xva_history(start: date, end: date, frequency: str = "auto") -> XvaHistory:
    """Desk totals per day: from the desk history file where it has been landed, otherwise summed from the
    official counterparty values."""
    if start > end:
        raise DataNotAvailable("start must not be later than end.")
    with db.connect(_store_path(), read_only=True) as con:
        has_history = db.table_exists(con, "xva_desk_total")
    rows: list[tuple] = []
    basis = "Sum of official counterparty values"
    if has_history:
        rows = _rows(
            "SELECT as_of, measure, value FROM xva_desk_total WHERE as_of BETWEEN ? AND ? ORDER BY as_of",
            [start, end],
        )
        if rows:
            basis = "Desk total history file (XVA system)"
    if not rows:
        rows = _rows(
            """
            SELECT as_of, measure, SUM(value) FROM xva_value WHERE as_of BETWEEN ? AND ?
            GROUP BY as_of, measure ORDER BY as_of
            """,
            [start, end],
        )
    all_points = _points(rows)
    if not all_points:
        raise DataNotAvailable(f"No desk XVA totals between {start} and {end}.")
    first, last = all_points[0], all_points[-1]
    if frequency == "auto":
        frequency = "daily" if len(all_points) <= 66 else "month_end"
    points = all_points
    if frequency == "month_end":
        month_ends: dict[tuple[int, int], XvaPoint] = {}
        for p in all_points:
            month_ends[(p.as_of.year, p.as_of.month)] = p
        points = list(month_ends.values())
        if points[0].as_of != first.as_of:
            points.insert(0, first)
        basis += ", month-end points (plus the first date)"
    d = dictionary()
    return XvaHistory(
        start=start,
        end=end,
        basis=basis,
        points=points,
        first=first,
        last=last,
        change=last.total - first.total,
        provenance=Provenance(
            source=basis,
            as_of=last.as_of,
            run_ids=[],
            unit=d["reporting_currency"],
            sign_convention=d["sign_convention"],
            generated_at=_now(),
        ),
    )


# ---------------------------------------------------------------- sensitivities


def sensitivity_components() -> list[str]:
    return list(dictionary().get("sensitivity_components") or ["XVA"])


def sensitivities(
    as_of: date,
    asset_class: str,
    measure: str | None,
    group_by: str,
    counterparty_id: str | None,
    top_n: int,
    component: str | None = None,
) -> SensitivityReport:
    _require_date(as_of)
    defined = dictionary()["sensitivities"].get(asset_class, {})
    if not defined:
        raise DataNotAvailable(f"No sensitivities are defined for {asset_class}. Defined: {', '.join(dictionary()['sensitivities'])}.")
    measure = measure or next(iter(defined))
    spec = defined.get(measure)
    if spec is None:
        raise DataNotAvailable(f"No {measure} sensitivity is defined for {asset_class}. Use one of: {', '.join(defined)}.")
    allowed = sensitivity_components()
    component = (component or allowed[0]).upper()
    if component not in allowed:
        raise DataNotAvailable(f"Sensitivities are stored for {', '.join(allowed)}, not {component}.")
    key_expr, label_expr = _SENS_GROUP[group_by]
    where = "s.as_of = ? AND s.asset_class = ? AND s.measure = ? AND COALESCE(s.component, 'XVA') = ?"
    params: list[Any] = [as_of, asset_class, measure, component]
    if counterparty_id:
        where += " AND s.counterparty_id = ?"
        params.append(counterparty_id)
    sql = f"""
        SELECT {key_expr} AS key, {label_expr} AS label, SUM(s.value) AS value
        FROM xva_sensitivity s JOIN dim_counterparty c ON c.counterparty_id = s.counterparty_id
        WHERE {where}
        GROUP BY 1, 2
        HAVING SUM(s.value) <> 0
    """
    rows = sorted(
        sorted((SensRow(key=k, label=lbl, value=v) for k, lbl, v in _rows(sql, params)), key=lambda r: r.key),
        key=lambda r: abs(r.value),
        reverse=True,
    )
    return SensitivityReport(
        as_of=as_of,
        asset_class=asset_class,  # type: ignore[arg-type]
        measure=measure,
        component=component,
        group_by=group_by,  # type: ignore[arg-type]
        counterparty_id=counterparty_id,
        rows=rows[:top_n],
        total=sum(r.value for r in rows),
        truncated=len(rows) > top_n,
        provenance=_provenance([as_of], unit=spec["unit"]),
    )


# ---------------------------------------------------------------- P&L attribution


def pnl_components() -> list[str]:
    """Components P&L explain is stored for; the first is the default. Empty = rows are summed as stored."""
    return list(dictionary().get("pnl_components") or [])


def pnl_attribution(start: date, end: date, level: str, key: str | None,
                    component: str | None = None) -> PnlAttribution:
    if start >= end:
        raise DataNotAvailable("start must be earlier than end.")
    allowed = pnl_components()
    component = (component or (allowed[0] if allowed else "")).upper() or None
    if component and allowed and component not in allowed:
        raise DataNotAvailable(f"P&L explain is stored for {', '.join(allowed)}, not {component}.")
    total_label = str(dictionary().get("total_label", "XVA")).upper()
    value_measures = total_components() if component in (None, total_label) else [component]
    _require_date(start)
    _require_date(end)
    if level == "counterparty" and not key:
        raise DataNotAvailable("Counterparty level needs a counterparty_id.")
    params = [key] if key else []

    def where(t: str) -> str:
        return f" AND {_FILTER[level].format(t=t)} = ?" if key else ""

    values = _rows(
        f"""
        SELECT v.as_of, SUM(v.value)
        FROM xva_value v JOIN dim_counterparty c ON c.counterparty_id = v.counterparty_id
        WHERE v.as_of IN (?, ?) AND v.measure IN ({db.placeholders(len(value_measures))}){where("v")}
        GROUP BY v.as_of
        """,
        [start, end, *value_measures, *params],
    )
    by_date = dict(values)
    if end not in by_date:
        raise DataNotAvailable(f"No XVA values for that selection on {end}.")
    xva_change = by_date.get(end, 0.0) - by_date.get(start, 0.0)

    drivers = _rows(
        f"""
        SELECT p.driver, SUM(p.value)
        FROM xva_pnl_explain p JOIN dim_counterparty c ON c.counterparty_id = p.counterparty_id
        WHERE p.as_of > ? AND p.as_of <= ?{" AND p.measure = ?" if component else ""}{where("p")}
        GROUP BY p.driver
        """,
        [start, end, *([component] if component else []), *params],
    )
    if not drivers:
        raise DataNotAvailable(
            f"No {component + ' ' if component else ''}P&L explain is loaded for {start} to {end}. "
            f"The change in {component or 'total XVA'} is {xva_change:,.0f}; "
            "use get_xva_movers for the change by counterparty or entity and component."
        )
    order = list(dictionary().get("pnl_drivers") or {})
    group_of = _driver_grouper()
    driver_rows = sorted(
        (DriverRow(driver=d, value=v, group=group_of(d)) for d, v in drivers),
        key=lambda r: order.index(r.driver) if r.driver in order else len(order),
    )
    group_order = list(dictionary().get("pnl_driver_groups") or {}) + ["Other"]
    group_sums: dict[str, float] = {}
    for r in driver_rows:
        if r.group:
            group_sums[r.group] = group_sums.get(r.group, 0.0) + r.value
    groups = [DriverRow(driver=g, value=group_sums[g], group=g) for g in group_order if g in group_sums]

    with db.connect(_store_path(), read_only=True) as con:
        has_actual = db.table_exists(con, "xva_pnl_actual")  # stores built before the table existed
    actual = [] if not has_actual else _rows(
        f"""
        SELECT SUM(a.actual_pl), SUM(a.expected_pl), COUNT(a.actual_pl), COUNT(a.expected_pl)
        FROM xva_pnl_actual a JOIN dim_counterparty c ON c.counterparty_id = a.counterparty_id
        WHERE a.as_of > ? AND a.as_of <= ?{" AND a.measure = ?" if component else ""}{where("a")}
        """,
        [start, end, *([component] if component else []), *params],
    )
    actual_pl = actual[0][0] if actual and actual[0][2] else None
    expected_pl = actual[0][1] if actual and actual[0][3] else None
    basis = "actual_pl" if actual_pl is not None else "xva_change"
    target = actual_pl if actual_pl is not None else xva_change

    unexplained = sum(r.value for r in driver_rows if r.driver == "unexplained")
    t = dictionary()["thresholds"]
    flag = abs(unexplained) > t["unexplained_abs_cad"] or (
        target != 0 and abs(unexplained) > t["unexplained_pct_of_change"] * abs(target)
    )
    return PnlAttribution(
        start=start,
        end=end,
        level=level,  # type: ignore[arg-type]
        key=key,
        component=component,
        xva_change=xva_change,
        actual_pl=actual_pl,
        expected_pl=expected_pl,
        groups=groups,
        drivers=driver_rows,
        unexplained=unexplained,
        unexplained_flag=flag,
        reconciliation_basis=basis,  # type: ignore[arg-type]
        reconciliation_gap=round(target - sum(r.value for r in driver_rows), 2),
        provenance=_provenance([start, end]),
    )


def _driver_grouper():
    """Driver -> group from pnl_driver_groups in the dictionary (group: [regex, ...]; first match wins)."""
    spec = dictionary().get("pnl_driver_groups") or {}
    compiled = [(g, [re.compile(p) for p in (pats if isinstance(pats, list) else [pats])]) for g, pats in spec.items()]

    def group_of(driver: str) -> str | None:
        if not compiled:
            return None
        return next((g for g, pats in compiled if any(p.search(driver) for p in pats)), "Other")

    return group_of


# ---------------------------------------------------------------- reference


def search_counterparties(query: str, limit: int) -> CounterpartySearch:
    rows = _rows(
        """
        SELECT counterparty_id, name, rating, sector, region, book, segment, credit_curve, customer_type, cpty_type
        FROM dim_counterparty
        WHERE upper(counterparty_id) LIKE upper(?) || '%' OR lower(name) LIKE '%' || lower(?) || '%'
        ORDER BY counterparty_id LIMIT ?
        """,
        [query, query, limit],
    )
    return CounterpartySearch(
        query=query,
        matches=[
            CounterpartyInfo(counterparty_id=r[0], name=r[1] or r[0], rating=r[2], sector=r[3], region=r[4], book=r[5],
                             segment=r[6], credit_curve=r[7], customer_type=r[8], cpty_type=r[9])
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
        "entities": d.get("entities", {}),
        "segments": d.get("segments", {}),
    }
    for asset_class, measures in d.get("sensitivities", {}).items():  # e.g. "pv01" as well as "IR"
        for name, entry in (measures or {}).items():
            if name.lower() == t.lower():
                return DefinitionResult(term=name, found=True, entry={"section": f"sensitivities.{asset_class}",
                                                                      "value": entry})
    for section, entries in sections.items():
        for name, entry in entries.items():
            if name.lower() == t.lower():
                return DefinitionResult(term=name, found=True, entry={"section": section, "value": entry})
    for name in ("sign_convention", "reporting_currency", "thresholds"):
        if name.lower() == t.lower().replace(" ", "_"):
            return DefinitionResult(term=name, found=True, entry={"section": "conventions", "value": d[name]})
    available = sorted({*sections["measures"], *sections["sensitivities"], *sections["pnl_drivers"]})
    return DefinitionResult(term=term, found=False, available_terms=available)


# ---------------------------------------------------------------- counterparty variants


def _variant_rows(sql: str, params: list[Any]) -> list[tuple]:
    """Variant tables keep one run per date and are never part of the official-run filter."""
    with db.connect(_store_path(), read_only=True) as con:
        return con.execute(sql, params).fetchall()


def compare_variants(counterparty_id: str, as_of: date, component: str | None = None,
                     pnl_start: date | None = None, top_n: int = 10) -> VariantComparison:
    """An official counterparty next to its test variants: values, sensitivities and P&L for one date."""
    with db.connect(_store_path(), read_only=True) as con:
        if not db.table_exists(con, "dim_counterparty_variant"):
            raise DataNotAvailable("No counterparty variants are loaded yet (landing/variants.yaml).")
    cp = counterparty_id.strip()
    found = _variant_rows("SELECT base_id FROM dim_counterparty_variant WHERE UPPER(variant_id) = UPPER(?)", [cp])
    base = found[0][0] if found else cp
    variants = _variant_rows(
        "SELECT variant_id, method, owner FROM dim_counterparty_variant WHERE UPPER(base_id) = UPPER(?) "
        "ORDER BY variant_id", [base])
    if not variants:
        bases = [r[0] for r in _variant_rows("SELECT DISTINCT base_id FROM dim_counterparty_variant ORDER BY 1", [])]
        raise DataNotAvailable(f"{cp} has no variants in variants.yaml. Counterparties with variants: "
                               f"{', '.join(bases[:20]) or 'none'}.")
    _require_date(as_of)
    comp = (component or "XVA").upper()
    if pnl_start is None:
        pnl_start = previous_business_date(as_of)
    codes = [base] + [v[0] for v in variants]
    info = {v[0]: (v[1], v[2]) for v in variants}
    ph = db.placeholders(len(codes) - 1)
    vcodes = codes[1:]
    missing: list[str] = []

    # values
    official_vals = dict(_rows("SELECT v.measure, SUM(v.value) FROM xva_value v WHERE v.as_of = ? "
                               "AND v.counterparty_id = ? GROUP BY v.measure", [as_of, base]))
    variant_vals: dict[str, dict[str, float]] = {}
    for vid, measure, value in _variant_rows(
            f"SELECT counterparty_id, measure, SUM(value) FROM xva_variant_value WHERE as_of = ? "
            f"AND counterparty_id IN ({ph}) GROUP BY 1, 2", [as_of, *vcodes]):
        variant_vals.setdefault(vid, {})[measure] = value
    base_total = _total(official_vals)
    value_rows = [VariantValueRow(counterparty_id=base, role="official", loaded=bool(official_vals),
                                  components=_ordered(official_vals), total=base_total)]
    if not official_vals:
        missing.append(f"{base}: no official values on {as_of}")
    for vid in vcodes:
        vals = variant_vals.get(vid, {})
        if not vals:
            missing.append(f"{vid}: no values loaded on {as_of}")
        value_rows.append(VariantValueRow(
            counterparty_id=vid, role="variant", method=info[vid][0], owner=info[vid][1], loaded=bool(vals),
            components=_ordered(vals), total=_total(vals),
            total_vs_official=_total(vals) - base_total if vals and official_vals else None))

    # sensitivities: by asset class + measure, and the risk factors that differ most
    sens: dict[tuple[str, str, str], dict[str, float]] = {}
    units: dict[tuple[str, str], str] = {}
    official_sens = _rows(
        "SELECT s.asset_class, s.measure, s.risk_factor, MAX(s.unit), SUM(s.value) FROM xva_sensitivity s "
        "WHERE s.as_of = ? AND s.counterparty_id = ? AND s.component = ? GROUP BY 1, 2, 3", [as_of, base, comp])
    variant_sens = _variant_rows(
        f"SELECT counterparty_id, asset_class, measure, risk_factor, MAX(unit), SUM(value) FROM xva_variant_sensitivity "
        f"WHERE as_of = ? AND counterparty_id IN ({ph}) AND component = ? GROUP BY 1, 2, 3, 4", [as_of, *vcodes, comp])
    for ac, m, rf, unit, v in official_sens:
        sens.setdefault((ac, m, rf), {})[base] = v
        units[(ac, m)] = unit
    have_sens = {r[0] for r in variant_sens}
    for vid, ac, m, rf, unit, v in variant_sens:
        sens.setdefault((ac, m, rf), {})[vid] = v
        units[(ac, m)] = unit
    if not official_sens and (have_sens or vcodes):
        missing.append(f"{base}: no official {comp} sensitivities on {as_of}")
    missing += [f"{vid}: no {comp} sensitivities loaded on {as_of}" for vid in vcodes if vid not in have_sens]

    def diff(vals: dict[str, float]) -> float | None:
        present = [v for v in vcodes if v in vals]
        if not present or base not in vals:
            return None
        return max(abs(vals[v] - vals[base]) for v in present)

    by_class: dict[tuple[str, str], dict[str, float]] = {}
    for (ac, m, _), vals in sens.items():
        agg = by_class.setdefault((ac, m), {})
        for k, v in vals.items():
            agg[k] = agg.get(k, 0.0) + v
    class_rows = [VariantSensRow(asset_class=ac, measure=m, unit=units.get((ac, m)),
                                 values={c: vals[c] for c in codes if c in vals}, largest_difference=diff(vals))
                  for (ac, m), vals in sorted(by_class.items())]
    factor_rows = sorted(sorted(
        (VariantSensRow(asset_class=ac, measure=m, risk_factor=rf, unit=units.get((ac, m)),
                        values={c: vals[c] for c in codes if c in vals}, largest_difference=diff(vals))
         for (ac, m, rf), vals in sens.items() if diff(vals)),
        key=lambda r: (r.asset_class, r.risk_factor or "", r.measure)),
        key=lambda r: r.largest_difference or 0.0, reverse=True)[:top_n]

    # P&L by driver group, and actual P&L
    group_of = _driver_grouper()
    pnl: dict[str, dict[str, float]] = {}
    start = pnl_start or as_of
    period = "a.as_of > ? AND a.as_of <= ?" if pnl_start else "a.as_of = ?"
    dates = [start, as_of] if pnl_start else [as_of]
    official_pnl = _rows(f"SELECT a.driver, SUM(a.value) FROM xva_pnl_explain a WHERE {period} "
                         "AND a.counterparty_id = ? AND a.measure = ? GROUP BY 1", [*dates, base, comp])
    variant_pnl = _variant_rows(f"SELECT counterparty_id, driver, SUM(value) FROM xva_variant_pnl_explain a "
                                f"WHERE {period} AND counterparty_id IN ({ph}) AND measure = ? GROUP BY 1, 2",
                                [*dates, *vcodes, comp])
    for drv, v in official_pnl:
        g = group_of(drv) or drv
        pnl.setdefault(g, {})[base] = pnl.get(g, {}).get(base, 0.0) + v
    for vid, drv, v in variant_pnl:
        g = group_of(drv) or drv
        pnl.setdefault(g, {})[vid] = pnl.get(g, {}).get(vid, 0.0) + v
    order = list(dictionary().get("pnl_driver_groups") or {}) + ["Other"]
    pnl_rows = [VariantPnlRow(group=g, values={c: pnl[g].get(c, 0.0) for c in codes})
                for g in sorted(pnl, key=lambda g: order.index(g) if g in order else len(order))]
    actual: dict[str, float] = {}
    with db.connect(_store_path(), read_only=True) as con:
        has_actual = db.table_exists(con, "xva_pnl_actual")
    if has_actual:
        for v, n in _rows(f"SELECT SUM(a.actual_pl), COUNT(a.actual_pl) FROM xva_pnl_actual a WHERE {period} "
                          "AND a.counterparty_id = ? AND a.measure = ?", [*dates, base, comp]):
            if n:
                actual[base] = v
    for vid, v in _variant_rows(f"SELECT counterparty_id, SUM(actual_pl) FROM xva_variant_pnl_actual a WHERE {period} "
                                f"AND counterparty_id IN ({ph}) AND measure = ? AND actual_pl IS NOT NULL GROUP BY 1",
                                [*dates, *vcodes, comp]):
        actual[vid] = v
    have_pnl = {r[0] for r in variant_pnl}
    missing += [f"{vid}: no {comp} P&L loaded for the period" for vid in vcodes if vid not in have_pnl]

    runs = [r[0] for r in _variant_rows("SELECT run_id FROM data_status WHERE as_of = ? AND source = 'xva_variants'",
                                        [as_of])]
    return VariantComparison(
        as_of=as_of, pnl_start=pnl_start, base_id=base, component=comp, counterparties=codes, values=value_rows,
        sensitivities=class_rows, risk_factor_differences=factor_rows, pnl_groups=pnl_rows, actual_pl=actual,
        missing=missing, variant_run_ids=runs, provenance=_provenance([as_of]),
    )
