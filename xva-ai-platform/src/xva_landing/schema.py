"""Tables of the curated store, shared by the sample-data builder and the landing jobs.

    python -m xva_landing.schema --store C:\\xva_data\\store\\xva_store.duckdb     # create an empty store

Long fact tables keyed by as_of + run_id. A restated day is landed as a new run; data_status records which
run is official for each date and source, and the tools read only that run.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from xva_mcp import db

TABLES: dict[str, str] = {
    "dim_counterparty": "counterparty_id VARCHAR PRIMARY KEY, name VARCHAR, rating VARCHAR, sector VARCHAR, "
    "region VARCHAR, desk VARCHAR, book VARCHAR, segment VARCHAR, credit_curve VARCHAR, customer_type VARCHAR, "
    "cpty_type VARCHAR, attributes_as_of DATE",
    "xva_value": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, netting_set_id VARCHAR, measure VARCHAR, "
    "value DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "xva_pnl_explain": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, driver VARCHAR, "
    "value DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "xva_pnl_actual": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, actual_pl DOUBLE, "
    "expected_pl DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "xva_sensitivity": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, asset_class VARCHAR, risk_factor VARCHAR, "
    "bucket VARCHAR, measure VARCHAR, value DOUBLE, unit VARCHAR, legal_entity VARCHAR, component VARCHAR",
    "xva_cpty_curve": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, credit_curve VARCHAR, "
    "tenor_months INTEGER, spread DOUBLE",
    "xva_desk_total": "as_of DATE, measure VARCHAR, value DOUBLE, run_id VARCHAR, source_file VARCHAR, "
    "loaded_at TIMESTAMPTZ",
    "data_status": "as_of DATE, source VARCHAR, run_id VARCHAR, loaded_at TIMESTAMPTZ, checks_passed BOOLEAN, "
    "notes VARCHAR",
    "mfl_result": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, value DOUBLE, "
    "source_file_sha256 VARCHAR, loaded_by VARCHAR, loaded_at TIMESTAMPTZ",
    # Counterparty variants (test calculations such as XVATEST-CHT): separate tables, so no official query or
    # total can include them. One run per date is kept (a reload replaces it).
    "dim_counterparty_variant": "variant_id VARCHAR PRIMARY KEY, base_id VARCHAR, method VARCHAR, owner VARCHAR, "
    "updated_at TIMESTAMPTZ",
    "xva_variant_value": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, netting_set_id VARCHAR, "
    "measure VARCHAR, value DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "xva_variant_sensitivity": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, asset_class VARCHAR, "
    "risk_factor VARCHAR, bucket VARCHAR, measure VARCHAR, value DOUBLE, unit VARCHAR, legal_entity VARCHAR, "
    "component VARCHAR",
    "xva_variant_pnl_explain": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, "
    "driver VARCHAR, value DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "xva_variant_pnl_actual": "as_of DATE, run_id VARCHAR, counterparty_id VARCHAR, measure VARCHAR, "
    "actual_pl DOUBLE, expected_pl DOUBLE, unit VARCHAR, legal_entity VARCHAR",
    "landing_log": "loaded_at TIMESTAMPTZ, as_of DATE, source VARCHAR, run_id VARCHAR, dataset VARCHAR, "
    "file_name VARCHAR, file_sha256 VARCHAR, rows_loaded INTEGER, loaded_by VARCHAR, outcome VARCHAR",
}


def ensure_schema(con: db.Connection) -> None:
    """Create missing tables, and add columns that newer kit versions introduced to existing stores."""
    for name, cols in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({cols})")
        have = set(db.table_columns(con, name))
        for col in cols.split(", "):
            col_name, col_type = col.split(" ", 1)
            if col_name not in have and "PRIMARY KEY" not in col_type:
                con.execute(f"ALTER TABLE {name} ADD COLUMN {col_name} {col_type}")


def columns(table: str) -> list[str]:
    return [c.split(" ", 1)[0] for c in TABLES[table].split(", ")]


def insert_sql(table: str, cols: list[str]) -> str:
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({db.placeholders(len(cols))})"


def create_store(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with db.connect(path) as con:
        ensure_schema(con)
    return path


def rename_measure(path: str | Path, old: str, new: str) -> dict[str, int]:
    """Rename an XVA component in every table that stores components, e.g. DVACO -> DVA."""
    counts = {}
    with db.connect(path) as con:
        ensure_schema(con)
        with con.transaction():
            for table in ("xva_value", "xva_pnl_explain", "xva_pnl_actual", "xva_desk_total", "xva_variant_value",
                          "xva_variant_pnl_explain", "xva_variant_pnl_actual"):
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE measure = ?", [old]).fetchone()[0]
                con.execute(f"UPDATE {table} SET measure = ? WHERE measure = ?", [new, old])
                counts[table] = n
    return counts


def main() -> None:
    p = argparse.ArgumentParser(description="Create an empty curated store (or add missing tables)")
    p.add_argument("--store", required=True, help="path ending .duckdb (DuckDB) or .sqlite (SQLite)")
    p.add_argument("--rename-measure", nargs=2, metavar=("OLD", "NEW"), help="rename a component, e.g. DVACO DVA")
    a = p.parse_args()
    path = create_store(a.store)
    print(f"Store ready: {path} ({db.backend_for(path)}); tables: {', '.join(TABLES)}")
    if a.rename_measure:
        counts = rename_measure(path, *a.rename_measure)
        print(f"Renamed {a.rename_measure[0]} to {a.rename_measure[1]}: "
              + ", ".join(f"{t} {n} rows" for t, n in counts.items()))


if __name__ == "__main__":
    main()
