"""Plain-text rendering of tool results, for the command line and the demo script.

ASCII only, so it prints in any Windows console and in Copilot's terminal. Numbers are shown in full
with thousands separators; the unit and sign convention come from each result's provenance.
"""

from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel

from .models import (
    CounterpartySearch,
    CounterpartyTimeseries,
    DataStatus,
    DefinitionResult,
    PnlAttribution,
    Provenance,
    SensitivityReport,
    XvaHistory,
    XvaMovers,
    XvaPoint,
    XvaSummary,
)


def money(x: float | None) -> str:
    if x is None:
        return ""
    whole = round(x)
    return f"{(whole or 0):,}"  # 'or 0' turns -0 into 0


def num(x: float | None) -> str:
    """Sensitivities: whole numbers when large, more decimals when small (FX gamma, vols)."""
    if x is None:
        return ""
    a = abs(x)
    if a >= 1000:
        return money(x)
    return f"{x:,.2f}" if a >= 1 else f"{x:,.4f}"


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]], numeric_from: int = 99) -> str:
    """Fixed-width table; columns at index >= numeric_from are right-aligned."""
    cells = [[str(h) for h in headers]] + [["" if c is None else str(c) for c in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]

    def line(row: Sequence[str]) -> str:
        parts = [c.rjust(w) if i >= numeric_from else c.ljust(w) for i, (c, w) in enumerate(zip(row, widths))]
        return "  ".join(parts).rstrip()

    out = [line(cells[0]), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in cells[1:]]
    return "\n".join(out)


def provenance(p: Provenance) -> str:
    runs = ", ".join(p.run_ids) or "n/a"
    return f"Source: {p.source} | as of {p.as_of} | runs: {runs} | unit: {p.unit} | {p.sign_convention}"


def _status(r: DataStatus) -> str:
    head = f"Data status for {r.as_of}: " + ("all sources passed" if r.all_passed else "WARNING - not all sources passed")
    rows = [(s.source, s.run_id, "passed" if s.checks_passed else "FAILED", s.notes or "") for s in r.sources]
    rows += [(m, "", "MISSING", "") for m in r.missing_sources]
    tail = (
        f"Previous business date: {r.previous_business_date} | month-to-date base: {r.month_start_reference}"
        f" | latest fully clean date: {r.latest_clean_date}"
    )
    return "\n".join([head, table(["Source", "Run", "Checks", "Notes"], rows), tail])


def _names(components: dict[str, float]) -> list[str]:
    return list(components)


def _summary(r: XvaSummary) -> str:
    names = _names(r.totals.components)
    rows = [(x.label, *(money(x.components.get(n)) for n in names), money(x.total)) for x in r.rows]
    t = r.totals
    rows.append(("Total (all groups)", *(money(t.components.get(n)) for n in names), money(t.total)))
    note = " (top rows only)" if r.truncated else ""
    return "\n".join(
        [f"XVA by {r.level} on {r.as_of}{note}", table([r.level.title(), *names, "Total XVA"], rows, 1)]
    )


def _movers(r: XvaMovers) -> str:
    names = _names(r.rows[0].component_changes) if r.rows else []
    rows = [
        (m.label, money(m.total_start), money(m.total_end), money(m.change),
         *(money(m.component_changes.get(n)) for n in names))
        for m in r.rows
    ]
    note = " (largest first, top rows only)" if r.truncated else " (largest first)"
    return "\n".join(
        [
            f"XVA change by {r.level}, {r.start} to {r.end}{note}",
            table([r.level.title(), str(r.start), str(r.end), "Change", *(f"{n} chg" for n in names)], rows, 1),
            f"Total change (all groups): {money(r.total_change)}",
        ]
    )


def _point_rows(points: list[XvaPoint]) -> tuple[list[str], list[tuple]]:
    names = _names(points[0].components) if points else []
    return names, [(p.as_of, *(money(p.components.get(n)) for n in names), money(p.total)) for p in points]


def _timeseries(r: CounterpartyTimeseries) -> str:
    names, rows = _point_rows(r.points)
    return "\n".join(
        [f"Daily XVA for {r.name} ({r.counterparty_id})", table(["Date", *names, "Total XVA"], rows, 1)]
    )


def _history(r: XvaHistory) -> str:
    names, rows = _point_rows(r.points)
    lines = [f"Desk XVA {r.start} to {r.end} ({r.basis})", table(["Date", *names, "Total XVA"], rows, 1)]
    if r.first and r.last:
        lines.append(f"Change in total XVA from {r.first.as_of} to {r.last.as_of}: {money(r.change)}")
    return "\n".join(lines)


def _sens(r: SensitivityReport) -> str:
    rows = [(x.label, num(x.value)) for x in r.rows]
    scope = f" for {r.counterparty_id}" if r.counterparty_id else ""
    note = " (top rows only)" if r.truncated else ""
    return "\n".join(
        [
            f"{r.component} {r.asset_class} {r.measure} by {r.group_by}{scope} on {r.as_of}{note}",
            table([r.group_by.replace("_", " ").title(), "Value"], rows, 1),
            f"Total: {num(r.total)}",
        ]
    )


def _pnl(r: PnlAttribution) -> str:
    scope = f"{r.level} {r.key}" if r.key else "whole desk"
    scope = f"{r.component} " + scope if r.component else scope
    lines = [f"XVA P&L explain, {scope}, after {r.start} up to {r.end}"]
    if r.groups:
        rows = [(g.driver, money(g.value)) for g in r.groups]
        rows.append(("Sum of drivers", money(sum(d.value for d in r.drivers))))
        lines += ["By driver group", table(["Group", "Value"], rows, 1), "By driver"]
        lines.append(table(["Driver", "Group", "Value"], [(d.driver, d.group or "", money(d.value)) for d in r.drivers], 2))
    else:
        rows = [(d.driver, money(d.value)) for d in r.drivers]
        rows.append(("Sum of drivers", money(sum(d.value for d in r.drivers))))
        lines.append(table(["Driver", "Value"], rows, 1))
    if r.actual_pl is not None:
        lines.append(f"Actual P&L (P&L system): {money(r.actual_pl)}")
    if r.expected_pl is not None:
        lines.append(f"Total expected P&L (P&L system): {money(r.expected_pl)}")
    what = "actual P&L" if r.reconciliation_basis == "actual_pl" else "change"
    lines += [
        f"Change in {r.component or 'XVA'} from official values: {money(r.xva_change)}",
        f"Reconciliation gap ({what} minus drivers): {money(r.reconciliation_gap)}",
    ]
    if r.unexplained_flag:
        lines.append(f"WARNING - unexplained {money(r.unexplained)} breaches the desk threshold")
    return "\n".join(lines)


def _search(r: CounterpartySearch) -> str:
    if not r.matches:
        return f"No counterparty matches '{r.query}'."
    rows = [(m.counterparty_id, m.name, m.rating or "", m.credit_curve or "", m.customer_type or "",
             m.cpty_type or "", m.segment or "", m.sector or "", m.book or "") for m in r.matches]
    return table(["ID", "Name", "Rating", "Credit curve", "Customer type", "Cpty type", "Segment", "Sector", "Book"],
                 rows)


def _definition(r: DefinitionResult) -> str:
    if not r.found:
        return f"No definition for '{r.term}'. Known terms: {', '.join(r.available_terms)}"
    entry = r.entry or {}
    value = entry.get("value")
    if isinstance(value, dict):
        body = "\n".join(f"  {k}: {v}" for k, v in value.items())
    else:
        body = f"  {value}"
    return f"{r.term} ({entry.get('section')}):\n{body}"


_RENDERERS = {
    DataStatus: _status,
    XvaSummary: _summary,
    XvaMovers: _movers,
    CounterpartyTimeseries: _timeseries,
    XvaHistory: _history,
    SensitivityReport: _sens,
    PnlAttribution: _pnl,
    CounterpartySearch: _search,
    DefinitionResult: _definition,
}


def render(result: BaseModel) -> str:
    """Readable text for any tool result, ending with its provenance line when it has one."""
    fn = _RENDERERS.get(type(result))
    text = fn(result) if fn else result.model_dump_json(indent=2)
    prov = getattr(result, "provenance", None)
    if isinstance(prov, Provenance):
        text += "\n" + provenance(prov)
    return text
