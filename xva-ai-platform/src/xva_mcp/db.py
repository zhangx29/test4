"""One connection layer for the curated store, so the rest of the code never imports a database driver.

Two backends are supported. Both run the same SQL, which is kept to the subset they share:
  - sqlite  (default) uses Python's built-in sqlite3 module: nothing to install.
  - duckdb  needs the optional package:  pip install -e ".[duckdb]"

Which one is used:
  1. XVA_STORE_BACKEND=sqlite|duckdb, if set;
  2. otherwise a path ending in .duckdb means duckdb;
  3. otherwise sqlite.

A production move to the bank's SQL platform adds a third backend here; store.py and the landing jobs
do not change.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import quote

BACKENDS = ("sqlite", "duckdb")
DEFAULT_STORE_PATH = "xva_store.sqlite"


class BackendUnavailable(RuntimeError):
    """The chosen backend's driver is not installed."""


def backend_for(path: str | Path) -> str:
    chosen = os.environ.get("XVA_STORE_BACKEND", "").strip().lower()
    if chosen:
        if chosen not in BACKENDS:
            raise ValueError(f"XVA_STORE_BACKEND must be one of {', '.join(BACKENDS)}; got {chosen!r}.")
        return chosen
    return "duckdb" if Path(path).suffix.lower() == ".duckdb" else "sqlite"


def duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return False
    return True


def _import_duckdb() -> Any:
    try:
        import duckdb
    except ImportError as exc:
        raise BackendUnavailable(
            "The duckdb package is not installed. Install it with: pip install -e \".[duckdb]\", "
            "or use SQLite: set XVA_STORE_BACKEND=sqlite and point XVA_STORE_PATH at a .sqlite file."
        ) from exc
    return duckdb


# ---------------------------------------------------------------- SQLite type handling
# Dates and timestamps are stored as ISO-8601 text and booleans as 0/1. Converters, keyed on the
# declared column type, turn them back into Python objects, so both backends return the same types.

_converters_registered = False


def _register_sqlite_converters() -> None:
    global _converters_registered
    if _converters_registered:
        return
    sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))
    sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()))
    sqlite3.register_converter("TIMESTAMPTZ", lambda b: datetime.fromisoformat(b.decode()))
    sqlite3.register_converter("BOOLEAN", lambda b: bool(int(b)))
    _converters_registered = True


_NULL = "\\N"


def _sqlite_value(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return int(v)
    return v


class Connection:
    """Thin wrapper with the same calls on both backends: execute, executemany and transaction."""

    def __init__(self, raw: Any, backend: str):
        self.raw = raw
        self.backend = backend

    def _params(self, params: Sequence[Any] | None) -> list[Any]:
        params = list(params or [])
        return [_sqlite_value(p) for p in params] if self.backend == "sqlite" else params

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        return self.raw.execute(sql, self._params(params))

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self.raw.executemany(sql, [self._params(r) for r in rows])

    def bulk_insert(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> int:
        """Insert many rows fast. DuckDB inserts row by row slowly through executemany, so rows go through a
        temporary CSV file and COPY; SQLite uses executemany. Returns the number of rows."""
        rows = list(rows)
        if not rows:
            return 0
        if self.backend != "duckdb":
            sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders(len(columns))})"
            self.executemany(sql, rows)
            return len(rows)
        import csv
        import os
        import tempfile

        fd, tmp = tempfile.mkstemp(suffix=".csv", prefix="xva_bulk_")
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(columns)
                for r in rows:
                    w.writerow([_NULL if v is None else _sqlite_value(v) for v in r])
            path = tmp.replace("\\", "/").replace("'", "''")
            self.raw.execute(
                f"COPY {table} ({', '.join(columns)}) FROM '{path}' (FORMAT csv, HEADER true, NULLSTR '{_NULL}')"
            )
        finally:
            os.remove(tmp)
        return len(rows)

    @contextmanager
    def transaction(self) -> Iterator["Connection"]:
        self.raw.execute("BEGIN TRANSACTION")
        try:
            yield self
        except BaseException:
            self.raw.execute("ROLLBACK")
            raise
        self.raw.execute("COMMIT")


def _read_only_uri(path: str | Path) -> str:
    """file: URI that opens an SQLite file read-only. Works for C:\\..., /data/... and \\\\server\\share\\... paths."""
    posix = Path(path).resolve().as_posix()
    if not posix.startswith("/"):
        posix = "/" + posix  # C:/dev/x -> /C:/dev/x
    return "file://" + quote(posix, safe="/:") + "?mode=ro"


@contextmanager
def connect(path: str | Path, read_only: bool = False) -> Iterator[Connection]:
    """Open the store. Read-only connections cannot change it on either backend."""
    backend = backend_for(path)
    if backend == "duckdb":
        raw = _import_duckdb().connect(database=str(path), read_only=read_only)
    else:
        _register_sqlite_converters()
        if read_only:
            raw = sqlite3.connect(_read_only_uri(path), uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
        else:
            raw = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
        raw.isolation_level = None  # explicit transactions only, the same as DuckDB
    try:
        yield Connection(raw, backend)
    finally:
        raw.close()


def placeholders(n: int) -> str:
    """'?, ?, ?' for an IN list; both backends accept it, unlike DuckDB's list parameters."""
    if n < 1:
        raise ValueError("An IN list needs at least one value.")
    return ", ".join("?" for _ in range(n))


def table_exists(con: Connection, name: str) -> bool:
    if con.backend == "duckdb":
        sql = "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?"
    else:
        sql = "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?"
    return bool(con.execute(sql, [name]).fetchone()[0])


def table_columns(con: Connection, name: str) -> list[str]:
    """Column names of a table (PRAGMA table_info works on both backends)."""
    return [r[1] for r in con.raw.execute(f"PRAGMA table_info('{name}')").fetchall()]

