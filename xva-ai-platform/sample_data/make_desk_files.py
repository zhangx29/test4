"""Write fictional files in the same layout as the desk's XVA CSV folder, for testing the desk loader.

    python sample_data/make_desk_files.py C:\\temp\\xva_csv_sample 2026-11-23 2026-11-24

Layout follows the desk's files: COB_DATE as m/d/yyyy h:mm, INTERNAL_ENTITY, CPTY_CODE, then values or one
column per risk factor. XVA = CVA + DVACO + FVA; XVA_Sens = CVA_Sens + FVA_Sens. All numbers are made up.
Also writes XVA_PnL_<CVA|FVA|XVA>_yyyymmdd.csv in the layout of the P&L procedure, for --source pnl_explain=csv.
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ENTITIES = ["BMO", "BCMC", "BCML"]
COUNTERPARTIES = [("BMO", f"{100 + i}{suffix}") for i, suffix in enumerate(["IN", "ONL", "BK", "SAS", "SL", "CA", "DE", "NI"])]
COUNTERPARTIES += [("BCMC", "LCH"), ("BCML", "BMO"), ("BMO", "1SEBK"), ("BMO", "145RICHM")]
SSA = {"1SEBK", "145RICHM"}
TENORS = ["6MO", "1YR", "2YR", "3YR", "5YR", "7YR", "10YR", "12YR", "15YR", "20YR", "25YR", "30YR"]
SHORT_TENORS = ["1YR", "2YR", "5YR", "10YR", "15YR", "20YR", "30YR"]


def _bal(name: str, n: int, lag: bool = False) -> list[str]:
    return ([f"{name}_DELTA_PARALLEL"] + [f"{name}_DELTA_BAL{i}YR" for i in range(n)] + [f"{name}_DELTA_BALMAX"]
            + ([f"{name}_DELTA_LAG"] if lag else []))


# The risk-factor columns of CVA_Sens / FVA_Sens / XVA_Sens, in the desk's order (header list of 2026-11-25)
SENS_COLUMNS = (
    [c for ccy in ("CAD", "USD", "EUR", "GBP") for c in [f"{ccy}_PV01"] + [f"{ccy}_PV01_{t}" for t in TENORS]]
    + ["CORRASOFR_PV01"] + [f"CORRASOFR_PV01_{t}" for t in SHORT_TENORS]
    + [c for ccy in ("AUD", "CHF") for c in [f"{ccy}_PV01"] + [f"{ccy}_PV01_{t}" for t in SHORT_TENORS]]
    + ["USD_IR_VOL", "CAD_IR_VOL", "EUR_IR_VOL", "GBP_IR_VOL"]
    + [f"{p}_SPOT" for p in ("USDCAD", "EURCAD", "CADJPY", "GBPCAD", "AUDCAD", "NZDCAD", "CADCHF", "CADCNH",
                             "CADCNY", "CADDKK", "CADHKD", "CADMXN", "CADNOK", "CADSEK", "CADSGD")]
    + ["CARRY_1D", "USDCAD_VOL", "EURUSD_VOL", "GBPUSD_VOL",
       "USDCAD_DNGAMMA", "USDCAD_UPGAMMA", "USDCAD_DNGAMMA_CAD01", "USDCAD_DNGAMMA_USD01",
       "USDCAD_UPGAMMA_CAD01", "USDCAD_UPGAMMA_USD01", "JPY_PV01"]
    + _bal("AECO_BASIS", 6) + _bal("NG", 9, lag=True) + _bal("OIL", 7) + _bal("NWROC_BASIS", 6)
    + _bal("WCS_BASIS", 3) + _bal("NGL", 3) + _bal("NBP", 2) + _bal("AL", 5) + _bal("CA", 3)
    + ["XAU_FX_PARALLEL", "XAG_FX_PARALLEL", "XAU_PARALLEL", "XAG_PARALLEL",
       "XPD_FX_PARALLEL", "XPT_FX_PARALLEL", "XPD_PARALLEL", "XPT_PARALLEL", "EQ_DELTA"]
)
NON_ZERO = {"CAD_PV01_6MO", "CAD_PV01_1YR", "CAD_PV01_2YR", "CAD_PV01_3YR", "CAD_PV01_5YR", "USD_PV01_1YR",
            "USD_PV01_2YR", "CAD_IR_VOL", "USDCAD_SPOT", "CARRY_1D", "USDCAD_VOL", "USDCAD_DNGAMMA",
            "USDCAD_UPGAMMA", "USDCAD_UPGAMMA_CAD01", "NG_DELTA_BAL0YR", "NG_DELTA_BAL1YR", "OIL_DELTA_BAL1YR",
            "AECO_BASIS_DELTA_BAL0YR", "XAU_FX_PARALLEL", "EQ_DELTA"}
CS_CURVES = ["BMO_CASH", "BMO_CDS", "CPTY_CDS"]
CS_TENORS = ["6m", "12m", "24m", "36m", "48m", "60m", "84m", "120m", "180m", "240m", "360m", "600m"]


# Columns of the P&L template procedure (APP_PNL_FVA2_REPORT.SP_GET_CptyFVAPnl_TimeSeries), in order.
PNL_DRIVERS = [
    "CPTY_CREDIT_NOCLOSEOUT", "CPTY_CREDIT_CLOSEOUT", "CPTY_CREDIT_ON_FVA", "BMO_CREDIT_NOCLOSEOUT",
    "BMO_CREDIT_CLOSEOUT", "BMO_CREDIT_ON_FVA", "BMO_FUNDING_ASSET", "BMO_FUNDING_LIABLITY", "DEFAULT_EVENTS",
    "CAD_RATES", "USD_RATES", "EUR_RATES", "CORRASOFR_RATES", "GBP_RATES", "AUD_RATES", "JPY_RATES", "CHF_RATES",
    "USD_IR_VEGA", "CAD_IR_VEGA", "EUR_IR_VEGA", "GBP_IR_VEGA",
    "USDCAD_DELTA", "EURCAD_DELTA", "CADJPY_DELTA", "GBPCAD_DELTA", "AUDCAD_DELTA", "NZDCAD_DELTA", "CADCHF_DELTA",
    "CADCNH_DELTA", "CADCNY_DELTA", "CADDKK_DELTA", "CADHKD_DELTA", "CADMXN_DELTA", "CADNOK_DELTA", "CADSEK_DELTA",
    "CADSGD_DELTA", "USDCAD_GAMMA", "USDCAD_VEGA", "EURUSD_VEGA", "GBPUSD_VEGA",
    "AECO_BASIS_COMMODITIES", "NG_COMMODITIES", "OIL_COMMODITIES", "NWROC_BASIS_COMMODITIES", "WCS_BASIS_COMMODITIES",
    "NGL_COMMODITIES", "NBP_COMMODITIES", "XAU_FX_COMMODITIES", "XAG_FX_COMMODITIES", "XAU_COMMODITIES",
    "XAG_COMMODITIES", "XPD_FX_COMMODITIES", "XPT_FX_COMMODITIES", "XPD_COMMODITIES", "XPT_COMMODITIES",
    "AL_COMMODITIES", "CA_COMMODITIES", "UNEXPLAINED_COMMODITIES", "EQ_DELTA", "CARRY", "DOCUMENTATION",
    "JOINT_DEFAULT_CORRELATION", "MODEL_CHANGES", "CALIBRATION", "NEW_TRADES", "UNWIND_TRADES",
]
PNL_TEMPORARY = ["TEMPORARY_TRADEEFFECTS", "TEMPORARY_MAPPINGISSUES", "TEMPORARY_TRAPPEDCOLLATERAL"]
PNL_COLUMNS = (["COB_DATE", "INTERNAL_ENTITY", "CPTY_CODE", "ACTUAL_PL"] + PNL_DRIVERS
               + ["Total Expected P/L"] + PNL_TEMPORARY + ["Unexplained P/L"])
# blank (not applicable) in the procedure's output for the CVA run, as in the desk's example
_PNL_BLANK = {"CPTY_CREDIT_ON_FVA", "BMO_CREDIT_ON_FVA", "BMO_FUNDING_ASSET", "BMO_FUNDING_LIABLITY", "JPY_RATES",
              "UNEXPLAINED_COMMODITIES", "DOCUMENTATION", "JOINT_DEFAULT_CORRELATION", "MODEL_CHANGES",
              "CALIBRATION", "UNWIND_TRADES", *PNL_TEMPORARY}


def _pnl_parts(d: date, cp: str) -> dict[str, dict[str, float]]:
    """Made-up daily P&L by driver for CVA and FVA; XVA = CVA + FVA driver by driver."""
    r = random.Random(f"pnl{cp}{d:%Y%m%d}")
    cva = {"CPTY_CREDIT_NOCLOSEOUT": r.uniform(-2000, 9000), "BMO_CREDIT_NOCLOSEOUT": r.uniform(0, 60),
           "CAD_RATES": r.uniform(-600, 600), "USD_RATES": r.uniform(-16000, 2000), "CORRASOFR_RATES": r.uniform(0, 2),
           "USD_IR_VEGA": r.uniform(0, 800), "USDCAD_DELTA": r.uniform(-1000, 10000), "USDCAD_GAMMA": r.uniform(-170, 0),
           "USDCAD_VEGA": r.uniform(-480, 0), "CARRY": r.uniform(-330, 2),
           "NEW_TRADES": r.uniform(-110000, 20000) if cp.endswith(("IN", "BK")) else 0.0}
    fva = {"BMO_FUNDING_ASSET": r.uniform(-900, 300), "BMO_FUNDING_LIABLITY": r.uniform(-200, 400),
           "CPTY_CREDIT_ON_FVA": r.uniform(-150, 150), "USD_RATES": r.uniform(-3000, 3000), "CARRY": r.uniform(-90, 10)}
    return {"CVA": {k: round(v, 4) for k, v in cva.items()}, "FVA": {k: round(v, 4) for k, v in fva.items()},
            "unexplained": {"CVA": round(r.uniform(-700, 450), 4), "FVA": round(r.uniform(-80, 80), 4)}}


def pnl_table(d: date, component: str, counterparties: list[tuple[str, str]] | None = None,
              cob_date: object = None) -> tuple[list[str], list[list]]:
    """Fictional output of the P&L procedure for one P_XVAType: one row per counterparty, one column per driver.
    Total Expected P/L = the drivers; ACTUAL_PL = Total Expected + temporary items + Unexplained P/L."""
    rows = []
    for ent, cp in counterparties or COUNTERPARTIES:
        if cp in ("LCH", "BMO"):
            continue
        parts = _pnl_parts(d, cp)
        comps = ["CVA", "FVA"] if component == "XVA" else [component]
        values = {k: sum(parts[c].get(k, 0.0) for c in comps) for k in PNL_DRIVERS}
        unexplained = sum(parts["unexplained"][c] for c in comps)
        blank = _PNL_BLANK if component == "CVA" else set(PNL_TEMPORARY) | {"JPY_RATES", "UNEXPLAINED_COMMODITIES"}
        expected = sum(values.values())
        row: list = [cob_date if cob_date is not None else cob(d), ent, cp, round(expected + unexplained, 4)]
        row += ["" if k in blank else round(values[k], 4) for k in PNL_DRIVERS]
        row += [round(expected, 4), "", "", "", round(unexplained, 4)]
        rows.append(row)
    return PNL_COLUMNS, rows


def cob(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year} 0:00"


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _values(rng: random.Random, d: date) -> dict[tuple[str, str], dict[str, float]]:
    out = {}
    for ent, cp in COUNTERPARTIES:
        if cp in ("LCH", "BMO"):
            out[(ent, cp)] = {"CVA": 0.0, "CVACO": 0.0, "DVACO": 0.0, "FVA": 0.0}
            continue
        base = random.Random(hash(cp) % 1000)
        drift = 1 + (d.toordinal() % 7) * 0.01
        out[(ent, cp)] = {
            "CVA": round(-base.uniform(50, 80000) * drift, 2),
            "CVACO": round(-base.uniform(0, 300) * (cp in SSA), 2),
            "DVACO": round(base.uniform(0, 400) * (cp in SSA), 2),
            "FVA": round(base.uniform(-6000, 9000) * drift, 2),
        }
    return out


def write_day(folder: Path, d: date, history_start: date) -> None:
    rng = random.Random(d.toordinal())
    ymd = d.strftime("%Y%m%d")
    # P&L explain, named as pnl_explain -> files -> pattern in desk_csv.yaml (read with --source pnl_explain=csv)
    for component in ("CVA", "FVA", "XVA"):
        _write(folder / f"XVA_PnL_{component}_{ymd}.csv", *pnl_table(d, component))
    vals = _values(rng, d)
    header = ["COB_DATE", "INTERNAL_ENTITY", "CPTY_CODE", "CVA", "CVACO", "DVACO", "FVA", "XVA"]

    def vrow(ent: str, cp: str) -> list:
        v = vals[(ent, cp)]
        return [cob(d), ent, cp, v["CVA"], v["CVACO"], v["DVACO"], v["FVA"], round(v["CVA"] + v["DVACO"] + v["FVA"], 2)]

    _write(folder / f"XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{ymd}.csv", header, [vrow(e, c) for e, c in COUNTERPARTIES])
    _write(folder / f"XVA_CVA_CVACO_DVACO_FVA_SSA_{ymd}.csv", header,
           [vrow(e, c) for e, c in COUNTERPARTIES if c in SSA])

    # desk history: every weekday from history_start to d
    hist, day = [], history_start
    while day <= d:
        if day.weekday() < 5:
            if day == d:
                t = {m: sum(v[m] for v in vals.values()) for m in ("CVA", "CVACO", "DVACO", "FVA")}
            else:
                k = 1 + (day.toordinal() % 11) * 0.002
                t = {m: sum(v[m] for v in vals.values()) * k for m in ("CVA", "CVACO", "DVACO", "FVA")}
            hist.append([f"{day.month}/{day.day}/{day.year}", *(round(t[m], 2) for m in ("CVA", "CVACO", "DVACO", "FVA")),
                         round(t["CVA"] + t["DVACO"] + t["FVA"], 2)])
        day += timedelta(days=1)
    _write(folder / f"XVA_CVA_CVACO_DVACO_FVA_{history_start:%Y%m%d}_{ymd}.csv",
           ["COB_DATE", "CVA", "CVACO", "DVACO", "FVA", "XVA"], hist)

    # sensitivities: the desk's full header; parallel columns = 1.002 x the sum of their buckets
    cols = SENS_COLUMNS
    per: dict[str, dict[tuple[str, str], list[float]]] = {"CVA": {}, "FVA": {}}
    for comp in per:
        for ent, cp in COUNTERPARTIES:
            r = random.Random(f"{comp}{cp}{ymd}")
            cells = {c: (round(r.uniform(-50, 50), 4) if c in NON_ZERO and cp not in ("LCH", "BMO") else 0.0)
                     for c in cols}
            for c in cols:
                if c.endswith("_PV01") or c.endswith("_DELTA_PARALLEL"):
                    stem = c[: -len("PARALLEL")] if c.endswith("PARALLEL") else c + "_"
                    cells[c] = round(sum(v for k, v in cells.items() if k.startswith(stem) and k != c
                                         and not k.endswith("LAG")) * 1.002, 4)
            per[comp][(ent, cp)] = [cells[c] for c in cols]
    per["XVA"] = {k: [round(a + b, 4) for a, b in zip(per["CVA"][k], per["FVA"][k])] for k in per["CVA"]}
    for comp, rows in per.items():
        detail = [[cob(d), e, c, *rows[(e, c)]] for e, c in COUNTERPARTIES]
        _write(folder / f"{comp}_Sens_{ymd}.csv", ["COB_DATE", "INTERNAL_ENTITY", "CPTY_CODE", *cols], detail)
        totals = [round(sum(r[i] for r in rows.values()), 4) for i in range(len(cols))]
        _write(folder / f"{comp}_Sens_Cons_{ymd}.csv", ["COB_DATE", *cols], [[cob(d), *totals]])
        _write(folder / f"{comp}_Sens_SSA_{ymd}.csv", ["COB_DATE", "INTERNAL_ENTITY", "CPTY_CODE", *cols],
               [r for r in detail if r[2] in SSA])

    # credit spreads (no date, no entity), then attributes, the spread curve and CPTY_CR01_1pct
    cs_cols = [c for curve in CS_CURVES for c in [f"{curve}_Parallel"] + [f"{curve}_{t}" for t in CS_TENORS]]
    months = [6, 12, 24, 36, 48, 60, 84, 120, 180, 240, 360, 600]
    extra = ["RATING", "CREDIT_CURVE", "CUSTOMER_TYPE", *(f"MTH{m}" for m in months), "CPTY_TYPE", "CPTY_CR01_1pct"]
    cs_rows = []
    for _, cp in COUNTERPARTIES:
        r = random.Random(f"cs{cp}{ymd}")
        row: list = []
        for _curve in CS_CURVES:
            buckets = [round(r.uniform(-20, 20), 2) if i < 6 else 0.0 for i in range(len(CS_TENORS))]
            row += [round(sum(buckets), 2)] + buckets
        rating = 30 + (sum(map(ord, cp)) % 15)
        base = 20 + rating
        row += [rating, f"FS:{rating}", "FS", *(round(base * (1 + 0.5 * (m / 600) ** 0.5), 4) for m in months),
                "LOW_IG" if rating > 38 else "S_P_D", round(r.uniform(-15, 0), 4)]
        cs_rows.append([cp, *row])
    _write(folder / f"XVA_ALL01_{ymd}.csv", ["CPTY_CODE", *cs_cols, *extra], cs_rows)


def build(folder: str | Path, dates: list[date], history_start: date = date(2026, 10, 1)) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for d in dates:
        write_day(folder, d, history_start)
    return folder


if __name__ == "__main__":
    args = sys.argv[1:] or ["sample_data/xva_csv_sample", "2026-11-23", "2026-11-24"]
    out = build(args[0], [date.fromisoformat(x) for x in args[1:]])
    print(f"Wrote fictional desk files to {out}")
