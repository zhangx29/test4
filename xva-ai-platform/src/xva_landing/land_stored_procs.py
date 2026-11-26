"""Run an allowlisted set of stored procedures with bound parameters under a read-only login.

Only procedures listed in ALLOWED can run, with exactly the parameters listed, in order.
Nothing from a user or an agent is ever concatenated into SQL.

    conn = pyodbc.connect(os.environ["XVA_SQL_DSN"])   # read-only service account
    rows = run_proc(conn, "xva.usp_GetCounterpartyCsa", {"as_of": date(2026, 11, 20)})

From the command line, export a procedure's result to CSV, then land it with land_extract:

    set XVA_SQL_CONN=DSN=XVA_READONLY        (an ODBC DSN or full connection string; read-only login)
    python -m xva_landing.land_stored_procs --proc xva.usp_GetCounterpartyStatic --param as_of=2026-11-20 ^
        --out C:\\xva_data\\inbox\\2026-11-20\\counterparties.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from typing import Any, Mapping, Sequence

# procedure name -> ordered parameter names. Replace with the bank's procedures.
ALLOWED: dict[str, tuple[str, ...]] = {
    "xva.usp_GetCounterpartyStatic": ("as_of",),
    "xva.usp_GetCounterpartyCsa": ("as_of",),
    "xva.usp_GetNettingSetMap": ("as_of",),
    "xva.usp_GetCollateralBalances": ("as_of", "counterparty_id"),
}


class ProcedureNotAllowed(ValueError):
    pass


def build_call(name: str, params: Mapping[str, Any]) -> tuple[str, list[Any]]:
    if name not in ALLOWED:
        raise ProcedureNotAllowed(f"{name} is not on the allowlist")
    expected = ALLOWED[name]
    if set(params) != set(expected):
        raise ProcedureNotAllowed(f"{name} expects parameters {list(expected)}, got {sorted(params)}")
    placeholders = ", ".join("?" for _ in expected)
    return f"{{CALL {name}({placeholders})}}", [params[p] for p in expected]


def run_proc(conn: Any, name: str, params: Mapping[str, Any]) -> list[Sequence[Any]]:
    sql, values = build_call(name, params)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, values)
        return cursor.fetchall()
    finally:
        cursor.close()


def run_proc_with_header(conn: Any, name: str, params: Mapping[str, Any]) -> tuple[list[str], list[Sequence[Any]]]:
    sql, values = build_call(name, params)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, values)
        header = [d[0] for d in cursor.description or []]
        return header, cursor.fetchall()
    finally:
        cursor.close()


def export_csv(header: list[str], rows: list[Sequence[Any]], out: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows:
            w.writerow([v.isoformat() if isinstance(v, date) else v for v in r])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run an allowlisted stored procedure and save its result as CSV")
    p.add_argument("--proc", required=True, help=f"one of: {', '.join(ALLOWED)}")
    p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE",
                   help="procedure parameter; dates as YYYY-MM-DD (repeatable)")
    p.add_argument("--out", required=True, help="CSV file to write")
    a = p.parse_args(argv)

    params: dict[str, Any] = {}
    for item in a.param:
        name, _, value = item.partition("=")
        try:
            params[name] = date.fromisoformat(value)
        except ValueError:
            params[name] = value
    try:
        build_call(a.proc, params)
    except ProcedureNotAllowed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    conn_str = os.environ.get("XVA_SQL_CONN")
    if not conn_str:
        print("Set XVA_SQL_CONN to the read-only ODBC DSN or connection string.", file=sys.stderr)
        return 2
    import pyodbc  # optional dependency: pip install -e ".[sql]"

    with pyodbc.connect(conn_str, readonly=True) as conn:
        header, rows = run_proc_with_header(conn, a.proc, params)
    export_csv(header, rows, a.out)
    print(f"Wrote {len(rows)} rows to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

