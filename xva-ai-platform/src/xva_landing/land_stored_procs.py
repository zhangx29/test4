"""Run an allowlisted set of stored procedures with bound parameters under a read-only login.

Only procedures listed in ALLOWED can run, with exactly the parameters listed, in order.
Nothing from a user or an agent is ever concatenated into SQL.

    conn = pyodbc.connect(os.environ["XVA_SQL_DSN"])   # read-only service account
    rows = run_proc(conn, "xva.usp_GetCounterpartyCsa", {"as_of": date(2026, 11, 20)})
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

# procedure name -> ordered parameter names. Replace with the bank's procedures.
ALLOWED: dict[str, tuple[str, ...]] = {
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
