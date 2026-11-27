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


def _counterparties(d: date) -> list[tuple[str, str]]:
    path = VALUES_FOLDER / f"XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d:%Y%m%d}.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["INTERNAL_ENTITY"], r["CPTY_CODE"]) for r in csv.DictReader(fh)]


def pnl_rows(d: date, xva_type: str) -> list[dict]:
    """The procedure's wide layout (one column per driver), dates as datetimes and blanks as None like MFL."""
    header, rows = pnl_table(d, xva_type, _counterparties(d), cob_date=datetime(d.year, d.month, d.day))
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
        return pnl_rows(params["p_PERIODSTART"], params["P_XVAType"])
    if sp.endswith("SP_GET_LITES_FVA01_TimeSeries"):
        return {"COB_DATE": [params["p_PERIODSTART"]], "FVA01": [float("nan")]}
    raise RuntimeError(f"unknown procedure {sp}")


class FakeBackend:
    def run(self, db: str, sp: str, params: dict):
        return run_sp(db, sp, params)

    def add_business_days(self, d: date, n: int, calendar: str) -> date:
        from xva_landing.sp_runner import weekday_shift

        return weekday_shift(d, n)
