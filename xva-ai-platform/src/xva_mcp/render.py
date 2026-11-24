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
    XvaMovers,
    XvaSummary,
)


def money(x: float | None) -> str:
    if x is None:
        return ""
    whole = round(x)
    return f"{(whole or 0):,}"  # 'or 0' turns -0 into 0


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


def _summary(r: XvaSummary) -> str:
    rows = [(x.label, money(x.cva), money(x.dva), money(x.fva), money(x.total)) for x in r.rows]
    t = r.totals
    rows.append(("Total (all groups)", money(t.cva), money(t.dva), money(t.fva), money(t.total)))
    note = " (top rows only)" if r.truncated else ""
    return "\n".join(
        [f"XVA by {r.level} on {r.as_of}{note}", table([r.level.title(), "CVA", "DVA", "FVA", "Total"], rows, 1)]
    )


def _movers(r: XvaMovers) -> str:
    rows = [
        (m.label, money(m.total_start), money(m.total_end), money(m.change), money(m.cva_change),
         money(m.dva_change), money(m.fva_change))
        for m in r.rows
    ]
    note = " (largest first, top rows only)" if r.truncated else " (largest first)"
    return "\n".join(
        [
            f"XVA change by {r.level}, {r.start} to {r.end}{note}",
            table([r.level.title(), str(r.start), str(r.end), "Change", "CVA chg", "DVA chg", "FVA chg"], rows, 1),
            f"Total change (all groups): {money(r.total_change)}",
        ]
    )


def _timeseries(r: CounterpartyTimeseries) -> str:
    rows = [(p.as_of, money(p.cva), money(p.dva), money(p.fva), money(p.total)) for p in r.points]
    return "\n".join(
        [f"Daily XVA for {r.name} ({r.counterparty_id})", table(["Date", "CVA", "DVA", "FVA", "Total"], rows, 1)]
    )


def _sens(r: SensitivityReport) -> str:
    rows = [(x.label, money(x.value)) for x in r.rows]
    scope = f" for {r.counterparty_id}" if r.counterparty_id else ""
    note = " (top rows only)" if r.truncated else ""
    return "\n".join(
        [
            f"{r.asset_class} {r.measure} by {r.group_by}{scope} on {r.as_of}{note}",
            table([r.group_by.replace("_", " ").title(), "Value"], rows, 1),
            f"Total: {money(r.total)}",
        ]
    )


def _pnl(r: PnlAttribution) -> str:
    scope = f"{r.level} {r.key}" if r.key else "whole desk"
    rows = [(d.driver, money(d.value)) for d in r.drivers]
    rows.append(("Sum of drivers", money(sum(d.value for d in r.drivers))))
    lines = [
        f"XVA P&L explain, {scope}, after {r.start} up to {r.end}",
        table(["Driver", "Value"], rows, 1),
        f"Change in XVA from official values: {money(r.xva_change)}",
        f"Reconciliation gap (change minus drivers): {money(r.reconciliation_gap)}",
    ]
    if r.unexplained_flag:
        lines.append(f"WARNING - unexplained {money(r.unexplained)} breaches the desk threshold")
    return "\n".join(lines)


def _search(r: CounterpartySearch) -> str:
    if not r.matches:
        return f"No counterparty matches '{r.query}'."
    rows = [(m.counterparty_id, m.name, m.rating, m.sector, m.region, m.book) for m in r.matches]
    return table(["ID", "Name", "Rating", "Sector", "Region", "Book"], rows)


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
