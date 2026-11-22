"""Land the manually run MFL workbook into the curated store.

Contract with the workbook (agree it with the MFL users, then lock the template):
  - named range XVA_ASOF          single cell, business date of the run
  - named range XVA_RESULTS       table with header: counterparty_id | measure | value
  - named range XVA_CONTROL_TOTAL single cell, sum of XVA_RESULTS.value computed in the workbook

The job reads cached values (the workbook must have been calculated and saved), records the file hash
and who ran it, applies the checks, writes rows idempotently, and publishes data_status for
source 'mfl_spreadsheet'. A failed check still publishes status, so the agent can say what is wrong.

    python -m xva_landing.land_mfl_workbook --file \\\\share\\xva\\mfl\\2026-11-20.xlsx --as-of 2026-11-20 \\
        --store /data/xva_store.duckdb
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from . import checks

HEADER = ["counterparty_id", "measure", "value"]
SOURCE = "mfl_spreadsheet"


@dataclass
class LandingResult:
    as_of: date
    run_id: str
    rows: int
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _named_range(wb: Any, name: str) -> list[list[Any]]:
    defined = wb.defined_names.get(name)
    if defined is None:
        raise KeyError(f"Named range {name} not found in workbook")
    out: list[list[Any]] = []
    for sheet, ref in defined.destinations:
        cells = wb[sheet][ref]
        if not isinstance(cells, tuple):  # single cell
            return [[cells.value]]
        for row in cells:
            out.append([c.value for c in (row if isinstance(row, tuple) else (row,))])
    return out


def read_workbook(path: Path) -> tuple[Any, list[list[Any]], Any]:
    from openpyxl import load_workbook  # optional dependency group "landing"

    wb = load_workbook(path, data_only=True, read_only=False)
    as_of_cell = _named_range(wb, "XVA_ASOF")[0][0]
    table = _named_range(wb, "XVA_RESULTS")
    control = _named_range(wb, "XVA_CONTROL_TOTAL")[0][0]
    return as_of_cell, table, control


def land(path: str | Path, as_of: date, store_path: str | Path, loaded_by: str | None = None) -> LandingResult:
    path = Path(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    run_id = f"MFL-{as_of:%Y%m%d}-{sha256[:8]}"
    as_of_cell, table, control = read_workbook(path)

    header, body = table[0], [r for r in table[1:] if any(v is not None for v in r)]
    failures = checks.expected_header(header, HEADER)
    failures += checks.as_of_matches(as_of_cell, as_of)
    failures += checks.no_excel_errors(body)
    numeric = [r for r in body if isinstance(r[2], (int, float))]
    if len(numeric) != len(body):
        failures.append(f"{len(body) - len(numeric)} rows have a non-numeric value")
    if isinstance(control, (int, float)):
        failures += checks.ties_to_control((r[2] for r in numeric), float(control))
    else:
        failures.append(f"Control total is not a number: {control!r}")

    now = datetime.now(timezone.utc)
    who = loaded_by or getpass.getuser()
    with duckdb.connect(str(store_path)) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS mfl_result (as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, "
            "measure VARCHAR, value DOUBLE, source_file_sha256 VARCHAR, loaded_by VARCHAR, loaded_at TIMESTAMPTZ)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS data_status (as_of DATE, source VARCHAR, run_id VARCHAR, "
            "loaded_at TIMESTAMPTZ, checks_passed BOOLEAN, notes VARCHAR)"
        )
        con.execute("BEGIN TRANSACTION")
        con.execute("DELETE FROM mfl_result WHERE as_of = ? AND run_id = ?", [as_of, run_id])
        if not failures:
            con.executemany(
                "INSERT INTO mfl_result VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(as_of, run_id, r[0], r[1], float(r[2]), sha256, who, now) for r in numeric],
            )
        con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [as_of, SOURCE])
        con.execute(
            "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
            [as_of, SOURCE, run_id, now, not failures, "; ".join(failures) or None],
        )
        con.execute("COMMIT")
    return LandingResult(as_of=as_of, run_id=run_id, rows=0 if failures else len(numeric), failures=failures)


def main() -> None:
    p = argparse.ArgumentParser(description="Land the MFL workbook into the curated store")
    p.add_argument("--file", required=True)
    p.add_argument("--as-of", required=True, type=date.fromisoformat)
    p.add_argument("--store", required=True)
    a = p.parse_args()
    result = land(a.file, a.as_of, a.store)
    print(f"{result.run_id}: {'PASSED' if result.passed else 'FAILED'} rows={result.rows}")
    for f in result.failures:
        print(f"  - {f}")
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
