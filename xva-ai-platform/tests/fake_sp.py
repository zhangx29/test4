"""A stand-in for MFL in tests: answers the desk's procedures with made-up tables, like DbUtils().Query would."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path

from sample_data.make_desk_files import pnl_table

CALLS: list[tuple[str, str, dict]] = []
VALUES_FOLDER: Path | None = None   # tests point this at the fictional desk CSV folder
FAIL: set[str] = set()              # P_XVAType values that should raise, to test failures
LAYOUT = "wide"                     # "long" answers one row per driver instead, to test that layout
# test variants the fake database holds: code -> (official counterparty, scale applied to its numbers)
VARIANTS = {"XVATEST-100IN": ("100IN", 1.25), "XVATEST-100IN2": ("100IN", 0.8), "XVATEST-102BK": ("102BK", 1.1)}
ID_COLUMNS = {"COB_DATE", "INTERNAL_ENTITY", "CPTY_CODE"}


def _select(rows: list[dict], params: dict) -> list[dict]:
    """Like the real procedures: P_ExcludedCpty drops codes containing any of its entries, P_CPTY_CODE keeps
    only the listed codes (comma-separated; None = all)."""
    excluded = [x.upper() for x in params.get("P_ExcludedCpty") or []]
    rows = [r for r in rows if not any(x in str(r["CPTY_CODE"]).upper() for x in excluded)]
    codes = params.get("P_CPTY_CODE")
    if codes:
        wanted = {c.strip().upper() for c in str(codes).split(",")}
        rows = [r for r in rows if str(r["CPTY_CODE"]).upper() in wanted]
    return rows


def _with_variants(rows: list[dict]) -> list[dict]:
    """Add each variant as a copy of its official counterparty's rows with the numbers scaled."""
    out = list(rows)
    for code, (base, scale) in VARIANTS.items():
        for r in rows:
            if r["CPTY_CODE"] == base:
                copy = {k: (v if k in ID_COLUMNS else _scaled(v, scale)) for k, v in r.items()}
                copy["CPTY_CODE"] = code
                out.append(copy)
    return out


def _scaled(v, scale):
    try:
        return round(float(v) * scale, 6)
    except (TypeError, ValueError):  # blanks and text attributes such as CREDIT_CURVE stay as they are
        return v


def _file_rows(name: str) -> list[dict]:
    with open(VALUES_FOLDER / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _counterparties(d: date) -> list[tuple[str, str]]:
    path = VALUES_FOLDER / f"XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d:%Y%m%d}.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["INTERNAL_ENTITY"], r["CPTY_CODE"]) for r in csv.DictReader(fh)]


def pnl_rows(d: date, xva_type: str) -> list[dict]:
    """The procedure's wide layout (one column per driver), dates as datetimes and blanks as None like MFL."""
    cps = _counterparties(d)
    cps += [(ent, code) for code, (base, _) in VARIANTS.items() for ent, cp in list(cps) if cp == base]
    header, rows = pnl_table(d, xva_type, cps, cob_date=datetime(d.year, d.month, d.day))
    out = [{h: (None if v == "" else v) for h, v in zip(header, row)} for row in rows]
    if LAYOUT == "long":
        out = [{"COB_DATE": r["COB_DATE"], "INTERNAL_ENTITY": r["INTERNAL_ENTITY"], "CPTY_CODE": r["CPTY_CODE"],
                "PNL_DRIVER": k, "PNL": v} for r in out for k, v in r.items()
               if k not in header[:4] and k != "Total Expected P/L" and v is not None]
    return out


def run_sp(db: str, sp: str, params: dict):
    CALLS.append((db, sp, params))
    if sp.endswith("SP_GET_CptyFVAPnl_TimeSeries"):
        if params["P_XVAType"] in FAIL:
            raise RuntimeError(f"ORA-20001: {params['P_XVAType']} P&L not ready")
        assert params["p_PERIODSTART"] == params["p_PERIODEND"] and isinstance(params["p_PERIODSTART"], date)
        rows = _select(pnl_rows(params["p_PERIODSTART"], params["P_XVAType"]), params)
        return rows
    d = params.get("p_PERIODSTART")
    if sp == "APP_TEST.SP_GET_XVA_BY_CPTY":  # values, as in the by_Cpty file
        return _select(_with_variants(_file_rows(f"XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d:%Y%m%d}.csv")), params)
    if sp == "APP_TEST.SP_GET_XVA_ALL01":    # credit spread sensitivities, attributes and curve
        return _select(_with_variants(_file_rows(f"XVA_ALL01_{d:%Y%m%d}.csv")), params)
    if sp == "APP_TEST.SP_GET_XVA_SENS":     # sensitivities, as in the CVA/FVA/XVA_Sens files
        return _select(_with_variants(_file_rows(f"{params['P_XVAType']}_Sens_{d:%Y%m%d}.csv")), params)
    if sp.endswith("SP_GET_LITES_FVA01_TimeSeries"):
        return {"COB_DATE": [params["p_PERIODSTART"]], "FVA01": [float("nan")]}
    raise RuntimeError(f"unknown procedure {sp}")


class FakeBackend:
    def run(self, db: str, sp: str, params: dict):
        return run_sp(db, sp, params)

    def add_business_days(self, d: date, n: int, calendar: str) -> date:
        from xva_landing.sp_runner import weekday_shift

        return weekday_shift(d, n)
