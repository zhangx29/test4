"""Run the desk's stored procedures from Python and save each result as a CSV the loader can read.

Procedures are defined in landing/stored_procs.yaml (an allowlist). The default backend is MFL, called exactly
as in the desk's "LITES templates" notebook:

    import pymfl_setup
    pymfl_setup.start_mfl(pymfl_setup.registered_mfl(), False)
    import pymfl
    pymfl.translator.translate(pymfl.DbUtils().Query(db, sp, names, values, None))

MFL holds the database connection and credentials; nothing here stores a password. A procedure result is
written to CSV before it is loaded, so procedure and CSV inputs go through the same checks and every load
keeps a copy of exactly what it read.

    python -m xva_landing.sp_runner --config landing\\desk_csv.yaml --list
    python -m xva_landing.sp_runner --config landing\\desk_csv.yaml --proc fva_pnl --as-of t-1 --show 20
    python -m xva_landing.sp_runner --config landing\\desk_csv.yaml --proc fva_pnl --as-of 2026-11-26 --out fva.csv
"""

from __future__ import annotations

import argparse
import csv
import importlib
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

_SAFE = re.compile(r"^[A-Za-z0-9_.@$#-]{1,128}$")
_PLACEHOLDER = re.compile(r"^\{(as_of|start|end|cpty_codes)\}$")


class ProcedureError(Exception):
    """A procedure is not allowed, badly defined, or failed to run. The message is safe to show."""


class Backend(Protocol):
    def run(self, db: str, sp: str, params: dict[str, Any]) -> Any: ...

    def add_business_days(self, d: date, n: int, calendar: str) -> date: ...


# ---------------------------------------------------------------- backends


class MflBackend:
    """pymfl, as in the desk notebook. Started once per process, on first use."""

    def __init__(self) -> None:
        self._pymfl: Any = None
        self._db: Any = None

    def _start(self) -> Any:
        if self._pymfl is None:
            try:
                import pymfl_setup  # type: ignore[import-not-found]

                pymfl_setup.start_mfl(pymfl_setup.registered_mfl(), False)
                import pymfl  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ProcedureError(
                    "MFL (pymfl) is not available in this Python. Run the loader with the same Python you use "
                    "for MFL notebooks, or set source: csv for this dataset."
                ) from exc
            self._pymfl = pymfl
            self._db = pymfl.DbUtils()
        return self._pymfl

    def run(self, db: str, sp: str, params: dict[str, Any]) -> Any:
        pymfl = self._start()
        result = self._db.Query(db, sp, list(params.keys()), list(params.values()), None)
        return pymfl.translator.translate(result)

    def add_business_days(self, d: date, n: int, calendar: str) -> date:
        pymfl = self._start()
        cal = pymfl.Dates().calendar(calendar)
        return _as_date(pymfl.translator.translate(pymfl.Calendar.addBusDays(cal, d, n, "FALSE")))


class CallableBackend:
    """backend: python -> callable: 'module:function' with signature run_sp(db, sp, params) -> table."""

    def __init__(self, target: str) -> None:
        module, _, func = target.partition(":")
        try:
            self._fn: Callable[..., Any] = getattr(importlib.import_module(module), func)
        except (ImportError, AttributeError) as exc:
            raise ProcedureError(f"Can't load backend callable {target!r}: {exc}") from exc

    def run(self, db: str, sp: str, params: dict[str, Any]) -> Any:
        return self._fn(db, sp, params)

    def add_business_days(self, d: date, n: int, calendar: str) -> date:
        return weekday_shift(d, n)


def weekday_shift(d: date, n: int) -> date:
    """Business days counting weekends only (no holidays); used when no MFL calendar is available."""
    step = 1 if n > 0 else -1
    while n:
        d += timedelta(days=step)
        if d.weekday() < 5:
            n -= step
    return d


def make_backend(registry: dict[str, Any]) -> Backend:
    kind = registry.get("backend", "mfl")
    if kind == "mfl":
        return MflBackend()
    if kind == "python":
        return CallableBackend(registry["callable"])
    raise ProcedureError(f"Unknown backend {kind!r} in stored_procs.yaml (use mfl or python)")


# ---------------------------------------------------------------- registry and parameters


@dataclass
class Procedure:
    name: str
    db: str
    sp: str
    params: dict[str, Any]
    description: str = ""


def load_registry(path: str | Path) -> dict[str, Any]:
    path = Path(os.path.expandvars(str(path)))
    if not path.exists():
        raise ProcedureError(f"Stored procedure file not found: {path}")
    reg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    procs: dict[str, Procedure] = {}
    for name, spec in (reg.get("procedures") or {}).items():
        for field_ in ("db", "sp"):
            value = str((spec or {}).get(field_, ""))
            if not _SAFE.match(value):
                raise ProcedureError(f"{path.name}: procedure {name} has a missing or unsafe {field_}: {value!r}")
        procs[name] = Procedure(name, spec["db"], spec["sp"], dict(spec.get("params") or {}),
                                spec.get("description", ""))
    reg["procedures"] = procs
    reg["_path"] = str(path)
    return reg


def render_params(proc: Procedure, start: date, end: date, as_of: date | None = None,
                  cpty_codes: list[str] | None = None) -> dict[str, Any]:
    """Fill placeholders. A placeholder must be the whole value, so typed values (dates) reach the procedure."""
    values = {
        "as_of": as_of or end,
        "start": start,
        "end": end,
        "cpty_codes": ",".join(cpty_codes) if cpty_codes else None,
    }
    out: dict[str, Any] = {}
    for key, value in proc.params.items():
        if isinstance(value, str):
            m = _PLACEHOLDER.match(value.strip())
            if m:
                out[key] = values[m.group(1)]
                continue
            if "{" in value:
                raise ProcedureError(f"{proc.name}.{key}: unknown placeholder in {value!r}")
        out[key] = list(value) if isinstance(value, list) else value
    return out


def run_procedure(backend: Backend, proc: Procedure, start: date, end: date, as_of: date | None = None,
                  cpty_codes: list[str] | None = None) -> tuple[list[str], list[list[str]]]:
    params = render_params(proc, start, end, as_of, cpty_codes)
    try:
        result = backend.run(proc.db, proc.sp, params)
    except ProcedureError:
        raise
    except Exception as exc:  # the database or MFL said no; keep the message for the status notes
        raise ProcedureError(f"{proc.name} ({proc.sp}) failed: {type(exc).__name__}: {exc}") from exc
    return to_table(result)


# ---------------------------------------------------------------- results


def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "to_pydatetime"):
        return v.to_pydatetime().date()
    return datetime.fromisoformat(str(v)[:10]).date()


def cell(v: Any) -> str:
    """Text for one CSV cell: blanks for None/NaN, ISO dates, plain numbers."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    if hasattr(v, "to_pydatetime"):  # pandas Timestamp
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if type(v).__name__ in ("NaTType",):
        return ""
    text = str(v)
    return "" if text.lower() in ("nan", "nat", "none") else text


def to_table(result: Any) -> tuple[list[str], list[list[str]]]:
    """Header and rows (as text) from a pandas DataFrame, a list of dicts, a dict of lists, or (header, rows)."""
    if result is None:
        return [], []
    if hasattr(result, "columns") and hasattr(result, "itertuples"):  # pandas DataFrame
        header = [str(c).strip() for c in result.columns]
        return header, [[cell(v) for v in row] for row in result.itertuples(index=False, name=None)]
    if isinstance(result, tuple) and len(result) == 2:
        header, rows = result
        return [str(h).strip() for h in header], [[cell(v) for v in r] for r in rows]
    if isinstance(result, dict):
        header = [str(k).strip() for k in result]
        cols = list(result.values())
        return header, [[cell(v) for v in row] for row in zip(*cols)]
    if isinstance(result, list):
        if not result:
            return [], []
        if isinstance(result[0], dict):
            header = list(result[0].keys())
            return [str(h).strip() for h in header], [[cell(r.get(h)) for h in header] for r in result]
    raise ProcedureError(f"Unexpected procedure result type {type(result).__name__}; expected a table")


def save_csv(header: list[str], rows: list[list[str]], path: str | Path,
             rename: dict[str, str] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [(rename or {}).get(h, h) for h in header]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def resolve_date(text: str, backend: Backend | None, calendar: str = "TOR",
                 today: date | None = None) -> date:
    """'2026-11-26', 't' (today), 't-1', 't-2' ... in business days on the MFL calendar (weekdays if MFL is off)."""
    text = text.strip().lower()
    m = re.fullmatch(r"t(?:-(\d+))?", text)
    if not m:
        return date.fromisoformat(text)
    today = today or date.today()
    n = int(m.group(1) or 0)
    if n == 0:
        return today
    if backend is not None:
        try:
            return backend.add_business_days(today, -n, calendar)
        except ProcedureError:
            pass
    return weekday_shift(today, -n)


# ---------------------------------------------------------------- command line


def registry_from_config(config: str | Path) -> dict[str, Any]:
    config = Path(config)
    cfg = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    reg_path = Path(os.path.expandvars(cfg.get("stored_procs", "stored_procs.yaml")))
    if not reg_path.is_absolute():
        reg_path = config.parent / reg_path
    return load_registry(reg_path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run a stored procedure from stored_procs.yaml")
    p.add_argument("--config", required=True, help="landing\\desk_csv.yaml (finds stored_procs.yaml)")
    p.add_argument("--list", action="store_true", help="list the procedures you may run")
    p.add_argument("--proc", help="procedure name from stored_procs.yaml")
    p.add_argument("--as-of", default="t-1", help="date: YYYY-MM-DD, t-1, t-2 ... (default t-1)")
    p.add_argument("--end", help="end of period (default = as-of)")
    p.add_argument("--cpty", help="comma-separated counterparty codes (default all)")
    p.add_argument("--show", type=int, metavar="N", help="print the columns and the first N rows")
    p.add_argument("--out", help="save the result as CSV here")
    a = p.parse_args(argv)
    try:
        reg = registry_from_config(a.config)
        if a.list or not a.proc:
            for name, proc in reg["procedures"].items():
                print(f"{name:<14} {proc.sp}  ({proc.description})")
            return 0
        proc = reg["procedures"].get(a.proc)
        if proc is None:
            raise ProcedureError(f"{a.proc} is not in {reg['_path']}")
        backend = make_backend(reg)
        start = resolve_date(a.as_of, backend, reg.get("calendar", "TOR"))
        end = resolve_date(a.end, backend, reg.get("calendar", "TOR")) if a.end else start
        header, rows = run_procedure(backend, proc, start, end, start,
                                     [c.strip() for c in a.cpty.split(",")] if a.cpty else None)
    except (ProcedureError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{proc.name}: {len(rows)} rows, {len(header)} columns for {start} to {end}")
    if a.show is not None:
        print("Columns: " + ", ".join(header))
        for r in rows[: a.show]:
            print("  " + " | ".join(r))
    if a.out:
        print(f"Saved {save_csv(header, rows, a.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
