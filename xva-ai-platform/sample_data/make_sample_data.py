"""Build a synthetic curated store so the MCP server, skills and evals can be exercised without bank data.

All counterparties and numbers are fictional. The P&L explain is constructed so that, per counterparty
and day, the drivers sum exactly to the change in XVA value; tests rely on that property.

    python sample_data/make_sample_data.py sample_data/xva_store.duckdb
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import duckdb

START, END = date(2026, 9, 30), date(2026, 11, 20)
MODEL_CHANGE_DAY = date(2026, 11, 2)
DESK = "XVA Desk"

COUNTERPARTIES = [
    # id, name, rating, sector, region, book
    ("CP001", "Northwind Energy Corp", "BBB", "Energy", "North America", "XVA-CORP"),
    ("CP002", "Maple Leaf Pension Fund", "AA", "Pension", "North America", "XVA-FIN"),
    ("CP003", "Atlas Shipping Ltd", "BB", "Industrials", "EMEA", "XVA-CORP"),
    ("CP004", "Pacific Rim Bank", "A", "Financials", "APAC", "XVA-FIN"),
    ("CP005", "Prairie Grain Co-op", "BBB-", "Agriculture", "North America", "XVA-CORP"),
    ("CP006", "Harbour City Municipality", "AA-", "Public sector", "North America", "XVA-CORP"),
    ("CP007", "Glacier Mining Inc", "BB+", "Materials", "North America", "XVA-CORP"),
    ("CP008", "Lakeshore Asset Management", "A-", "Financials", "North America", "XVA-FIN"),
]
EQ_NAMES = {"CP008"}
CO_NAMES = {"CP001", "CP007"}
DRIVERS = [
    "market_IR", "market_FX", "market_EQ", "market_CO", "market_CR", "time_decay",
    "new_trades", "amendments", "terminations", "model_or_parameter", "unexplained",
]


def business_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:  # sample only; production uses the bank's holiday calendar
            days.append(d)
        d += timedelta(days=1)
    return days


def _drivers(rng: random.Random, cp: str, measure: str, v: float, day: date) -> dict[str, float]:
    a = abs(v)
    cost = measure in ("CVA", "FVA")
    out = {
        "market_IR": rng.gauss(0, 0.020 * a),
        "market_FX": rng.gauss(0, 0.010 * a),
        "market_EQ": rng.gauss(0, 0.010 * a) if cp in EQ_NAMES else 0.0,
        "market_CO": rng.gauss(0, 0.015 * a) if cp in CO_NAMES else 0.0,
        "market_CR": rng.gauss(0, 0.025 * a),
        "time_decay": -0.002 * v,
        "new_trades": 0.0,
        "amendments": 0.0,
        "terminations": 0.0,
        "model_or_parameter": 0.0,
        "unexplained": rng.gauss(0, 0.002 * a),
    }
    if rng.random() < 0.10:
        out["new_trades"] = (-1 if cost else 1) * abs(rng.gauss(0.05 * a, 0.02 * a))
    if rng.random() < 0.03:
        out["amendments"] = rng.gauss(0, 0.02 * a)
    if rng.random() < 0.05:
        out["terminations"] = -0.03 * v
    if day == MODEL_CHANGE_DAY and measure == "CVA":
        out["model_or_parameter"] = -0.01 * a
    return out


def build(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(42)
    days = business_days(START, END)

    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE dim_counterparty (counterparty_id VARCHAR PRIMARY KEY, name VARCHAR, rating VARCHAR, "
        "sector VARCHAR, region VARCHAR, desk VARCHAR, book VARCHAR)"
    )
    con.execute(
        "CREATE TABLE xva_value (as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, netting_set_id VARCHAR, "
        "measure VARCHAR, value DOUBLE, unit VARCHAR)"
    )
    con.execute(
        "CREATE TABLE xva_pnl_explain (as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, "
        "driver VARCHAR, value DOUBLE, unit VARCHAR)"
    )
    con.execute(
        "CREATE TABLE xva_sensitivity (as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, asset_class VARCHAR, "
        "risk_factor VARCHAR, bucket VARCHAR, measure VARCHAR, value DOUBLE, unit VARCHAR)"
    )
    con.execute(
        "CREATE TABLE data_status (as_of DATE, source VARCHAR, run_id VARCHAR, loaded_at TIMESTAMPTZ, "
        "checks_passed BOOLEAN, notes VARCHAR)"
    )
    con.executemany(
        "INSERT INTO dim_counterparty VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(i, n, r, s, reg, DESK, b) for i, n, r, s, reg, b in COUNTERPARTIES],
    )

    level = {}
    for cp, *_ in COUNTERPARTIES:
        level[(cp, "CVA")] = -rng.uniform(0.3e6, 6.0e6)
        level[(cp, "DVA")] = rng.uniform(0.05e6, 1.2e6)
        level[(cp, "FVA")] = -rng.uniform(0.1e6, 2.5e6)

    values, explains, sens, status = [], [], [], []
    for i, day in enumerate(days):
        run_id = f"XVA-{day:%Y%m%d}-01"
        for cp, *_ in COUNTERPARTIES:
            for measure in ("CVA", "DVA", "FVA"):
                if i > 0:
                    drv = _drivers(rng, cp, measure, level[(cp, measure)], day)
                    level[(cp, measure)] += sum(drv.values())
                    explains += [(day, run_id, cp, measure, k, v, "CAD") for k, v in drv.items()]
                values.append((day, run_id, cp, f"{cp}-NS1", measure, level[(cp, measure)], "CAD"))

            cva = abs(level[(cp, "CVA")])
            drift = 1 + 0.01 * rng.gauss(0, 1)
            for rf, w in (("CAD-CORRA", 1.0), ("USD-SOFR", 0.6)):
                for bucket, bw in (("2Y", 0.4), ("5Y", 1.0), ("10Y", 0.8), ("30Y", 0.3)):
                    sens.append((day, run_id, cp, "IR", rf, bucket, "delta", -cva * 1e-4 * w * bw * drift, "CAD per bp"))
            sens.append((day, run_id, cp, "IR", "CAD-SWAPTION", "5Yx5Y", "vega", cva * 2e-4 * drift, "CAD per vol point"))
            for rf, w in (("USDCAD", 1.0), ("EURCAD", 0.3)):
                sens.append((day, run_id, cp, "FX", rf, "spot", "delta", -cva * 0.004 * w * drift, "CAD per 1%"))
            if cp in EQ_NAMES:
                sens.append((day, run_id, cp, "EQ", "SPX", "spot", "delta", -cva * 0.003 * drift, "CAD per 1%"))
            if cp in CO_NAMES:
                sens.append((day, run_id, cp, "CO", "WTI", "1Y", "delta", -cva * 0.006 * drift, "CAD per 1%"))
            for bucket, bw in (("1Y", 0.2), ("5Y", 1.0), ("10Y", 0.5)):
                sens.append((day, run_id, cp, "CR", f"CR-{cp}", bucket, "cs01", -cva * 1.2e-4 * bw * drift, "CAD per bp"))

        loaded = datetime.combine(day + timedelta(days=1), time(6, 30), tzinfo=timezone.utc)
        last = i == len(days) - 1
        status += [
            (day, "xva_system", run_id, loaded, True, None),
            (day, "stored_procs", f"SP-{day:%Y%m%d}", loaded, True, None),
            (
                day,
                "mfl_spreadsheet",
                f"MFL-{day:%Y%m%d}",
                loaded,
                not last,
                "Totals do not tie to control cell (difference 12,345 CAD)" if last else None,
            ),
        ]

    con.executemany("INSERT INTO xva_value VALUES (?, ?, ?, ?, ?, ?, ?)", values)
    con.executemany("INSERT INTO xva_pnl_explain VALUES (?, ?, ?, ?, ?, ?, ?)", explains)
    con.executemany("INSERT INTO xva_sensitivity VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", sens)
    con.executemany("INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)", status)
    con.close()
    return path


if __name__ == "__main__":
    out = build(sys.argv[1] if len(sys.argv) > 1 else "sample_data/xva_store.duckdb")
    print(f"Wrote {out}")
