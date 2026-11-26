"""Land real extracts (CSV or Excel) into the curated store: counterparties, XVA values, sensitivities, P&L explain.

Your files keep their own column names: landing_config.yaml maps them to the store's columns, and can turn
"wide" files (one column per measure or driver) into the store's long format. Every load is checked; a file
that fails is not loaded, and the failure is published in data_status so the agent and the CLI report it.

    # once, and whenever counterparty static changes
    python -m xva_landing.land_extract --config landing_config.yaml --store C:\\xva_data\\store\\xva_store.duckdb \\
        counterparties --file C:\\xva_data\\inbox\\2026-11-20\\counterparties.csv

    # every business day, after the official XVA run
    python -m xva_landing.land_extract --config landing_config.yaml --store C:\\xva_data\\store\\xva_store.duckdb \\
        xva --as-of 2026-11-20 --values values.csv --sens sensitivities.csv --pnl pnl_explain.csv \\
        --control values=-33572908.12 --archive C:\\xva_data\\archive

Exit code 0 = loaded and all checks passed, 1 = checks failed (nothing loaded), 2 = bad command or config.
Re-running the same files for the same date is safe. Loading different files for a date that already has an
official run is a restatement: the new run becomes official, the old rows stay for audit.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from xva_mcp import db

from . import checks
from .schema import ensure_schema

# store columns per dataset; the first group is required, "optional" may be left out of the mapping
DATASETS: dict[str, dict[str, list[str]]] = {
    "counterparties": {
        "required": ["counterparty_id", "name", "book"],
        "optional": ["rating", "sector", "region", "desk"],
    },
    "xva_values": {
        "required": ["counterparty_id", "measure", "value"],
        "optional": ["as_of", "netting_set_id", "legal_entity"],
    },
    "sensitivities": {
        "required": ["counterparty_id", "asset_class", "risk_factor", "measure", "value"],
        "optional": ["as_of", "bucket", "legal_entity", "component"],
    },
    "pnl_explain": {"required": ["counterparty_id", "measure", "driver", "value"], "optional": ["as_of", "legal_entity"]},
}
KEYS = {
    "xva_values": ("legal_entity", "counterparty_id", "netting_set_id", "measure"),
    "sensitivities": ("component", "legal_entity", "counterparty_id", "asset_class", "risk_factor", "bucket", "measure"),
    "pnl_explain": ("legal_entity", "counterparty_id", "measure", "driver"),
}


class ConfigError(Exception):
    pass


@dataclass
class Loaded:
    dataset: str
    path: Path
    sha256: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- reading and mapping


def load_config(path: str | Path) -> dict[str, Any]:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(cfg.get("datasets"), dict):
        raise ConfigError(f"{path}: expected a 'datasets:' section")
    return cfg


def read_table(path: Path, spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows as dicts keyed by the file's own header names (stripped)."""
    kind = (spec.get("file_type") or path.suffix.lstrip(".")).lower()
    if kind in ("xlsx", "xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[spec["sheet"]] if spec.get("sheet") else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        skip = int(spec.get("skip_rows", 0))
        for _ in range(skip):
            next(it, None)
        header = [str(h).strip() if h is not None else "" for h in next(it, [])]
        rows = [dict(zip(header, r)) for r in it if any(v not in (None, "") for v in r)]
        wb.close()
        return rows
    if kind in ("csv", "txt"):
        with open(path, newline="", encoding=spec.get("encoding", "utf-8-sig")) as fh:
            for _ in range(int(spec.get("skip_rows", 0))):
                fh.readline()
            reader = csv.DictReader(fh, delimiter=spec.get("delimiter", ","))
            reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
            return [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    raise ConfigError(f"{path.name}: unsupported file type {kind!r} (use csv or xlsx)")


def to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    text = str(v).strip().replace(",", "").replace(" ", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    try:
        x = float(text.strip("()"))
    except ValueError:
        return None
    return -x if negative else x


def to_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    text = str(v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def map_rows(dataset: str, raw: list[dict[str, Any]], spec: dict[str, Any], failures: list[str]) -> list[dict[str, Any]]:
    """Rename columns per the config, melt wide value columns, apply value maps and scale."""
    columns: dict[str, str] = spec.get("columns") or {}
    melt = spec.get("melt")  # {field: measure|driver, columns: {CVA: CVA_AMT, ...}}
    need = list(DATASETS[dataset]["required"])
    if melt:
        need = [c for c in need if c not in ("value", melt["field"])]
    header = set(raw[0]) if raw else set()
    missing = [f"{store_col} (file column '{columns.get(store_col, store_col)}')" for store_col in need
               if columns.get(store_col, store_col) not in header]
    if melt:
        missing += [f"{k} (file column '{v}')" for k, v in melt["columns"].items() if v not in header]
    if missing:
        failures.append(f"{dataset}: missing columns {', '.join(missing)}. Found: {', '.join(sorted(header))}")
        return []

    failures += [f"{dataset}: {x}" for x in checks.no_excel_errors(r.values() for r in raw)][:5]

    out: list[dict[str, Any]] = []
    wanted = DATASETS[dataset]["required"] + DATASETS[dataset]["optional"]
    for r in raw:
        base = {c: r.get(columns.get(c, c)) for c in wanted if columns.get(c, c) in r}
        if melt:
            for label, col in melt["columns"].items():
                out.append({**base, melt["field"]: label, "value": r.get(col)})
        else:
            out.append(base)

    maps: dict[str, dict[str, str]] = spec.get("value_maps") or {}
    scale = float(spec.get("scale", 1))
    for row in out:
        for col, mapping in maps.items():
            if col in row:
                key = _text(row[col])
                row[col] = mapping.get(key, key)
        if "value" in row:
            n = to_number(row["value"])
            row["value"] = None if n is None else n * scale
    return out


# ---------------------------------------------------------------- checks


def check_rows(dataset: str, rows: list[dict[str, Any]], as_of: date | None, dictionary: dict[str, Any],
               known_cps: set[str], failures: list[str]) -> None:
    if not rows:
        failures.append(f"{dataset}: no rows")
        return

    blank = sum(1 for r in rows if not _text(r.get("counterparty_id")))
    if blank:
        failures.append(f"{dataset}: {blank} rows have no counterparty_id")
    if dataset == "counterparties":
        ids = [_text(r.get("counterparty_id")) for r in rows]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            failures.append(f"counterparties: duplicate IDs {dupes[:5]}")
        return

    bad_values = sum(1 for r in rows if r.get("value") is None)
    if bad_values:
        failures.append(f"{dataset}: {bad_values} rows have a missing or non-numeric value")
    if as_of is not None and any("as_of" in r for r in rows):
        wrong = {str(to_date(r.get("as_of")) or r.get("as_of")) for r in rows if to_date(r.get("as_of")) != as_of}
        if wrong:
            failures.append(f"{dataset}: file dates {sorted(wrong)[:3]} do not match the business date {as_of}")

    unknown = sorted({_text(r["counterparty_id"]) for r in rows if _text(r.get("counterparty_id"))} - known_cps)
    if unknown:
        failures.append(
            f"{dataset}: {len(unknown)} counterparties not in the counterparty table, e.g. {unknown[:5]}. "
            "Load counterparties first."
        )

    if dataset in ("xva_values", "pnl_explain"):
        known = [m for m, spec in dictionary.get("measures", {}).items() if isinstance(spec, dict) and "in_total" in spec]
        bad = sorted({_text(r.get("measure")) for r in rows} - set(known))
        if bad:
            failures.append(f"{dataset}: unknown measures {bad[:5]}; map them to {known} in value_maps")
    if dataset == "pnl_explain":
        drivers = set(dictionary.get("pnl_drivers", {}))
        bad = sorted({_text(r.get("driver")) for r in rows} - drivers)
        if bad:
            failures.append(f"pnl_explain: unknown drivers {bad[:5]}; map them to {sorted(drivers)} in value_maps")
    if dataset == "sensitivities":
        sens = dictionary.get("sensitivities", {})
        bad = sorted({f"{_text(r.get('asset_class'))}/{_text(r.get('measure'))}" for r in rows
                      if _text(r.get("measure")) not in sens.get(_text(r.get("asset_class")), {})})
        if bad:
            failures.append(f"sensitivities: unknown asset class/measure pairs {bad[:5]} (see dictionary.yaml)")
        comps = dictionary.get("sensitivity_components") or ["XVA"]
        bad = sorted({_text(r.get("component")) or comps[0] for r in rows} - set(comps))
        if bad:
            failures.append(f"sensitivities: unknown components {bad[:5]}; stored components are {comps}")

    key = KEYS[dataset]
    seen: dict[tuple, int] = {}
    for r in rows:
        k = tuple(_text(r.get(c)) for c in key)
        seen[k] = seen.get(k, 0) + 1
    dupes = [k for k, n in seen.items() if n > 1]
    if dupes:
        failures.append(f"{dataset}: {len(dupes)} duplicate rows on {'+'.join(key)}, e.g. {list(dupes[0])}")


# ---------------------------------------------------------------- loading


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _archive(files: Iterable[Loaded], root: Path, as_of: date | None, source: str) -> None:
    folder = root / (as_of.isoformat() if as_of else "static") / source
    folder.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = folder / f"{f.path.stem}.{f.sha256[:12]}{f.path.suffix}"
        if not target.exists():
            shutil.copy2(f.path, target)


def _log(con: db.Connection, now: datetime, as_of: date | None, source: str, run_id: str, files: list[Loaded],
         who: str, outcome: str) -> None:
    con.executemany(
        "INSERT INTO landing_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(now, as_of, source, run_id, f.dataset, f.path.name, f.sha256, 0 if outcome != "loaded" else len(f.rows),
          who, outcome) for f in files],
    )


def land_counterparties(cfg: dict[str, Any], store: Path, path: Path, archive: Path | None = None) -> list[str]:
    spec = cfg["datasets"].get("counterparties") or {}
    f = Loaded("counterparties", path, _sha256(path))
    f.rows = map_rows("counterparties", read_table(path, spec), spec, f.failures)
    if f.rows:
        check_rows("counterparties", f.rows, None, {}, set(), f.failures)
    now, who = datetime.now(timezone.utc), getpass.getuser()
    with db.connect(store) as con:
        ensure_schema(con)
        with con.transaction():
            if not f.failures:
                for r in f.rows:
                    con.execute("DELETE FROM dim_counterparty WHERE counterparty_id = ?", [_text(r["counterparty_id"])])
                con.executemany(
                    "INSERT INTO dim_counterparty (counterparty_id, name, rating, sector, region, desk, book) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(_text(r["counterparty_id"]), _text(r.get("name")), _text(r.get("rating")) or None,
                      _text(r.get("sector")) or None, _text(r.get("region")) or None,
                      _text(r.get("desk")) or cfg.get("default_desk", "XVA Desk"), _text(r.get("book")))
                     for r in f.rows],
                )
            _log(con, now, None, "counterparties", f"CP-{f.sha256[:8]}", [f], who,
                 "loaded" if not f.failures else "rejected")
    if archive and not f.failures:
        _archive([f], archive, None, "counterparties")
    return f.failures


def land_xva(
    cfg: dict[str, Any],
    store: Path,
    as_of: date,
    files: dict[str, Path],
    source: str | None = None,
    run_id: str | None = None,
    controls: dict[str, float] | None = None,
    archive: Path | None = None,
    tolerance: float = 1.0,
) -> tuple[str, list[str], list[str]]:
    """Land one business date for one source. Returns (run_id, failures, notes)."""
    from xva_mcp import store as xstore

    source = source or cfg.get("source", "xva_system")
    dictionary = xstore.dictionary()
    loaded: list[Loaded] = []
    for dataset, path in files.items():
        spec = cfg["datasets"].get(dataset)
        if spec is None:
            raise ConfigError(f"landing config has no '{dataset}' section")
        f = Loaded(dataset, path, _sha256(path))
        f.rows = map_rows(dataset, read_table(path, spec), spec, f.failures)
        loaded.append(f)

    digest = hashlib.sha256("".join(sorted(f.sha256 for f in loaded)).encode()).hexdigest()
    run_id = run_id or f"{source.upper()}-{as_of:%Y%m%d}-{digest[:8]}"
    now, who = datetime.now(timezone.utc), getpass.getuser()

    with db.connect(store) as con:
        ensure_schema(con)
        known = {r[0] for r in con.execute("SELECT counterparty_id FROM dim_counterparty").fetchall()}
        for f in loaded:
            if f.rows:
                check_rows(f.dataset, f.rows, as_of, dictionary, known, f.failures)
            for name, control in (controls or {}).items():
                if name == f.dataset and f.rows and not f.failures:
                    f.failures += [f"{f.dataset}: {x}" for x in
                                   checks.ties_to_control((r["value"] for r in f.rows), control, tolerance)]
            prior = con.execute(
                f"SELECT COUNT(*) FROM {_TABLE[f.dataset]} t JOIN data_status ds ON ds.as_of = t.as_of "
                "AND ds.run_id = t.run_id AND ds.source = ? WHERE t.as_of = (SELECT MAX(as_of) FROM data_status "
                "WHERE source = ? AND checks_passed = TRUE AND as_of < ?)",
                [source, source, as_of],
            ).fetchone()[0]
            f.notes += [f"{f.dataset}: {x}" for x in checks.row_count_within(len(f.rows), prior or None)]

        failures = [x for f in loaded for x in f.failures]
        notes = [x for f in loaded for x in f.notes]
        previous = con.execute(
            "SELECT run_id, checks_passed FROM data_status WHERE as_of = ? AND source = ?", [as_of, source]
        ).fetchone()

        with con.transaction():
            for f in loaded:
                con.execute(f"DELETE FROM {_TABLE[f.dataset]} WHERE as_of = ? AND run_id = ?", [as_of, run_id])
            if not failures:
                for f in loaded:
                    _insert(con, f, as_of, run_id, cfg)
                con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [as_of, source])
                con.execute(
                    "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
                    [as_of, source, run_id, now, True, "; ".join(notes) or None],
                )
            elif previous and previous[1] and previous[0] != run_id:
                # a failed restatement: keep the official run, but say that a newer load was rejected
                con.execute(
                    "UPDATE data_status SET notes = ? WHERE as_of = ? AND source = ?",
                    [f"Restatement {run_id} rejected at {now:%Y-%m-%d %H:%M} UTC: " + "; ".join(failures),
                     as_of, source],
                )
            else:
                con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [as_of, source])
                con.execute(
                    "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
                    [as_of, source, run_id, now, False, "; ".join(failures)],
                )
            _log(con, now, as_of, source, run_id, loaded, who, "loaded" if not failures else "rejected")

    if archive:
        _archive(loaded, archive, as_of, source)
    return run_id, failures, notes


_TABLE = {"xva_values": "xva_value", "sensitivities": "xva_sensitivity", "pnl_explain": "xva_pnl_explain"}


def _insert(con: db.Connection, f: Loaded, as_of: date, run_id: str, cfg: dict[str, Any]) -> None:
    spec = cfg["datasets"][f.dataset]
    if f.dataset == "xva_values":
        unit = spec.get("unit", "CAD")
        con.executemany(
            "INSERT INTO xva_value (as_of, run_id, counterparty_id, netting_set_id, measure, value, unit, legal_entity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(as_of, run_id, _text(r["counterparty_id"]), _text(r.get("netting_set_id")) or None,
              _text(r["measure"]), r["value"], unit, _text(r.get("legal_entity")) or None) for r in f.rows],
        )
    elif f.dataset == "pnl_explain":
        unit = spec.get("unit", "CAD")
        con.executemany(
            "INSERT INTO xva_pnl_explain (as_of, run_id, counterparty_id, measure, driver, value, unit, legal_entity) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(as_of, run_id, _text(r["counterparty_id"]), _text(r["measure"]), _text(r["driver"]), r["value"], unit,
              _text(r.get("legal_entity")) or None) for r in f.rows],
        )
    else:
        from xva_mcp import store as xstore

        sens = xstore.dictionary().get("sensitivities", {})
        comps = xstore.dictionary().get("sensitivity_components") or ["XVA"]
        con.executemany(
            "INSERT INTO xva_sensitivity (as_of, run_id, counterparty_id, asset_class, risk_factor, bucket, measure, "
            "value, unit, legal_entity, component) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(as_of, run_id, _text(r["counterparty_id"]), _text(r["asset_class"]), _text(r["risk_factor"]),
              _text(r.get("bucket")) or None, _text(r["measure"]), r["value"],
              sens.get(_text(r["asset_class"]), {}).get(_text(r["measure"]), {}).get("unit", "CAD"),
              _text(r.get("legal_entity")) or None, _text(r.get("component")) or comps[0])
             for r in f.rows],
        )


# ---------------------------------------------------------------- command line


def _controls(items: list[str]) -> dict[str, float]:
    out = {}
    for item in items or []:
        name, _, value = item.partition("=")
        alias = {"values": "xva_values", "sens": "sensitivities", "pnl": "pnl_explain"}.get(name, name)
        n = to_number(value)
        if alias not in _TABLE or n is None:
            raise ConfigError(f"--control expects values=<number>, sens=<number> or pnl=<number>; got {item!r}")
        out[alias] = n
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Land real extracts into the curated XVA store")
    p.add_argument("--config", required=True, help="landing config YAML (see landing_config.example.yaml)")
    p.add_argument("--store", required=True, help="store file (.duckdb or .sqlite)")
    p.add_argument("--archive", help="folder for a copy of every input file (raw archive)")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("counterparties", help="load or update the counterparty table")
    c.add_argument("--file", required=True)
    x = sub.add_parser("xva", help="load one business date: values, and optionally sensitivities and P&L explain")
    x.add_argument("--as-of", required=True, type=date.fromisoformat)
    x.add_argument("--values", help="XVA values file")
    x.add_argument("--sens", help="sensitivities file")
    x.add_argument("--pnl", help="P&L explain file")
    x.add_argument("--source", help="source name in data_status (default from config, else xva_system)")
    x.add_argument("--run-id", help="run ID from the XVA system, if it has one (default: derived from the files)")
    x.add_argument("--control", action="append", metavar="DATASET=TOTAL",
                   help="control total from the official report, e.g. values=-33572908.12 (repeatable)")
    x.add_argument("--tolerance", type=float, default=1.0, help="allowed difference against control totals")
    a = p.parse_args(argv)

    try:
        cfg = load_config(a.config)
        store = Path(a.store)
        archive = Path(a.archive) if a.archive else None
        if a.command == "counterparties":
            failures = land_counterparties(cfg, store, Path(a.file), archive)
            print("Counterparties loaded." if not failures else "Counterparties REJECTED:\n  " + "\n  ".join(failures))
            return 0 if not failures else 1
        files = {k: Path(v) for k, v in (("xva_values", a.values), ("sensitivities", a.sens), ("pnl_explain", a.pnl))
                 if v}
        if "xva_values" not in files:
            raise ConfigError("--values is required: the tools read XVA values for every date")
        for path in files.values():
            if not path.exists():
                raise ConfigError(f"File not found: {path}")
        run_id, failures, notes = land_xva(cfg, store, a.as_of, files, a.source, a.run_id, _controls(a.control),
                                           archive, a.tolerance)
    except (ConfigError, KeyError, ValueError) as exc:
        print(f"Setup problem: {exc}", file=sys.stderr)
        return 2
    if failures:
        print(f"{a.as_of} run {run_id}: REJECTED, nothing loaded.\n  " + "\n  ".join(failures))
        return 1
    print(f"{a.as_of} run {run_id}: loaded and official. "
          + ", ".join(f"{k} from {v.name}" for k, v in files.items()))
    for n in notes:
        print(f"  note: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
