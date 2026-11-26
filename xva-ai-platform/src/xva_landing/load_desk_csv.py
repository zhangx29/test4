"""Load the desk's daily XVA CSV files (the "XVA CSV" folder) into the curated store.

One business date at a time, all files for that date in one transaction:
  - XVA_CVA_CVACO_DVACO_FVA_by_Cpty_yyyymmdd.csv  values per legal entity and counterparty (required)
  - CVA_Sens / FVA_Sens / XVA_Sens_yyyymmdd.csv    sensitivities per component, one column per risk factor
  - *_Sens_Cons_yyyymmdd.csv                       control totals for the sensitivity files
  - XVA_ALL01_yyyymmdd.csv                         credit spread sensitivities (BMO cash, BMO CDS, counterparty CDS)
  - XVA_CVA_CVACO_DVACO_FVA_SSA_yyyymmdd.csv       tags SSA counterparties
  - XVA_CVA_CVACO_DVACO_FVA_<start>_yyyymmdd.csv   desk totals history

What each file looks like is described in landing/desk_csv.yaml, so a renamed or added column is a config
change. Checks run before anything is written; a day that fails is not loaded and the reasons appear in
data_status. Loading the same files again is harmless; different files for a loaded date are a restatement.

    python -m xva_landing.load_desk_csv --config landing\\desk_csv.yaml --store C:\\Users\\me\\xva_data\\store\\xva_store.duckdb --list
    python -m xva_landing.load_desk_csv --config landing\\desk_csv.yaml --store ... --as-of 2026-11-24
    python -m xva_landing.load_desk_csv --config landing\\desk_csv.yaml --store ... --all     # every date not loaded yet

Exit codes: 0 every requested date loaded, 1 at least one date rejected, 2 setup problem.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import glob
import hashlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from xva_mcp import db

from .land_extract import to_number
from .schema import ensure_schema

VALUE_COLS = ["as_of", "run_id", "counterparty_id", "netting_set_id", "measure", "value", "unit", "legal_entity"]
ATTRIBUTE_COLS = ["rating", "credit_curve", "customer_type", "cpty_type", "segment"]
SENS_COLS = ["as_of", "run_id", "counterparty_id", "asset_class", "risk_factor", "bucket", "measure", "value", "unit",
             "legal_entity", "component"]


class SetupError(Exception):
    pass


@dataclass
class DayResult:
    as_of: date
    run_id: str = ""
    files: list[tuple[str, Path, str]] = field(default_factory=list)  # (dataset, path, sha256)
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    values: list[tuple] = field(default_factory=list)
    sens: list[tuple] = field(default_factory=list)
    counterparties: dict[str, str | None] = field(default_factory=dict)  # id -> segment
    attributes: dict[str, dict[str, str]] = field(default_factory=dict)  # id -> {rating, credit_curve, ...}
    curves: list[tuple] = field(default_factory=list)  # (as_of, run_id, cpty, credit_curve, months, spread)
    history: list[tuple[date, str, float]] = field(default_factory=list)
    history_file: str | None = None

    @property
    def loaded(self) -> bool:
        return not self.failures


# ---------------------------------------------------------------- config and files


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key in ("folder", "values"):
        if key not in cfg:
            raise SetupError(f"{path}: missing '{key}'")
    dict_path = Path(cfg.get("dictionary", "desk_dictionary.yaml"))
    if not dict_path.is_absolute():
        dict_path = path.parent / dict_path
    cfg["_dictionary"] = yaml.safe_load(dict_path.read_text(encoding="utf-8"))
    cfg["_dictionary_path"] = str(dict_path.resolve())
    measures = cfg["_dictionary"].get("measures", {})
    for comp in cfg["values"]["components"]:
        if not isinstance(measures.get(comp), dict) or "in_total" not in measures[comp]:
            raise SetupError(f"Component {comp} is not a measure with in_total in {dict_path}")
    return cfg


def _file(cfg: dict[str, Any], folder: Path, pattern: str, d: date) -> Path | None:
    matches = sorted(glob.glob(str(folder / pattern.replace("{yyyymmdd}", d.strftime("%Y%m%d")))))
    return Path(matches[0]) if matches else None


def available_dates(cfg: dict[str, Any], folder: Path) -> list[date]:
    pattern = cfg["values"]["pattern"]
    rx = re.compile("^" + re.escape(pattern).replace(re.escape("{yyyymmdd}"), r"(\d{8})") + "$", re.IGNORECASE)
    out = []
    for p in folder.glob(pattern.replace("{yyyymmdd}", "*")):
        m = rx.match(p.name)
        if m:
            try:
                out.append(datetime.strptime(m.group(1), "%Y%m%d").date())
            except ValueError:
                continue
    return sorted(set(out))


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in reader.fieldnames or []]
        reader.fieldnames = header
        rows = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    return header, rows


def parse_date(text: str, formats: list[str]) -> date | None:
    text = (text or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cut(items: list[str], n: int = 5) -> str:
    return ", ".join(items[:n]) + (f" and {len(items) - n} more" if len(items) > n else "")


def _report(res: DayResult, cfg: dict[str, Any], message: str) -> None:
    """A tie-out difference: rejects the day if strict_tieouts, otherwise a note in data_status."""
    (res.failures if cfg.get("strict_tieouts") else res.notes).append(message)


# ---------------------------------------------------------------- column rules


def compile_rules(rules: list[dict[str, Any]]) -> list[tuple[re.Pattern[str], dict[str, Any]]]:
    return [(re.compile(r["regex"]), r) for r in rules or []]


def classify(column: str, rules: list[tuple[re.Pattern[str], dict[str, Any]]]) -> tuple | None:
    """(asset_class, risk_factor, bucket, measure) for a risk-factor column, or None if no rule matches."""
    for rx, rule in rules:
        m = rx.match(column)
        if m:
            g = m.groupdict()
            bucket = rule.get("bucket")
            return (
                rule["asset_class"],
                rule["risk_factor"].format_map(g),
                bucket.format_map(g) if bucket else None,
                rule["measure"],
            )
    return None


# ---------------------------------------------------------------- reading one date


def _check_dates(res: DayResult, label: str, rows: list[dict[str, str]], col: str | None, formats: list[str]) -> None:
    if not col:
        return
    bad = sorted({r.get(col, "") for r in rows if parse_date(r.get(col, ""), formats) != res.as_of})
    if bad:
        res.failures.append(f"{label}: {col} values {bad[:3]} do not match the file date {res.as_of}")


def read_values(res: DayResult, cfg: dict[str, Any], path: Path) -> None:
    spec, dic = cfg["values"], cfg["_dictionary"]
    ids, comps, total_col = spec["id_columns"], spec["components"], spec.get("total_column")
    header, rows = read_csv(path)
    need = [c for c in (ids.get("as_of"), ids.get("legal_entity"), ids["counterparty_id"], *comps, total_col) if c]
    missing = [c for c in need if c not in header]
    if missing:
        res.failures.append(f"{path.name}: missing columns {missing}. Found: {header}")
        return
    if not rows:
        res.failures.append(f"{path.name}: no rows")
        return
    _check_dates(res, path.name, rows, ids.get("as_of"), cfg["date_formats"])

    in_total = [c for c in comps if dic["measures"][c]["in_total"]]
    tol = float(cfg.get("tolerance", 1.0))
    seen: set[tuple[str, str]] = set()
    dupes, bad_numbers, off_total = [], [], []
    for r in rows:
        cp = (r.get(ids["counterparty_id"]) or "").strip()
        ent = ((r.get(ids["legal_entity"]) or "").strip() or None) if ids.get("legal_entity") else None
        if not cp:
            res.failures.append(f"{path.name}: a row has no {ids['counterparty_id']}")
            continue
        if (ent or "", cp) in seen:
            dupes.append(f"{ent}/{cp}")
        seen.add((ent or "", cp))
        vals = {}
        for c in comps:
            v = to_number(r.get(c))
            if v is None:
                bad_numbers.append(f"{cp}.{c}={r.get(c)!r}")
                v = 0.0
            vals[c] = v
            res.values.append((res.as_of, None, cp, None, c, v, dic.get("reporting_currency", "CAD"), ent))
        if total_col:
            reported = to_number(r.get(total_col))
            if reported is None or abs(reported - sum(vals[c] for c in in_total)) > tol:
                off_total.append(f"{ent}/{cp}")
        res.counterparties.setdefault(cp, None)
    if dupes:
        res.failures.append(f"{path.name}: duplicate entity/counterparty rows: {_cut(dupes)}")
    if bad_numbers:
        res.failures.append(f"{path.name}: non-numeric values: {_cut(bad_numbers)}")
    if off_total:
        res.failures.append(
            f"{path.name}: {total_col} differs from {' + '.join(in_total)} by more than {tol} on {len(off_total)} rows "
            f"({_cut(off_total)}). Check which components make up total XVA in the dictionary."
        )


def read_sensitivity(res: DayResult, cfg: dict[str, Any], path: Path, component: str, ids: dict[str, str],
                     rules: list, label: str, skip: set[str] | None = None) -> dict[str, float]:
    """Melts one wide sensitivity file. Returns column totals (for control and sum checks).
    Columns in skip (attributes, curve levels) are handled by the caller."""
    header, rows = read_csv(path)
    id_cols = [c for c in ids.values() if c]
    missing = [c for c in id_cols if c not in header]
    if missing:
        res.failures.append(f"{path.name}: missing columns {missing}")
        return {}
    risk_cols = [c for c in header if c not in id_cols and c and c not in (skip or set())]
    mapping = {c: classify(c, rules) for c in risk_cols}
    unmapped = [c for c, m in mapping.items() if m is None]
    if unmapped:
        msg = f"{path.name}: {len(unmapped)} columns match no rule in desk_csv.yaml ({label}): {_cut(unmapped, 8)}"
        if cfg.get("unmapped_columns", "fail") == "fail":
            res.failures.append(msg + ". Add rules for them, or set unmapped_columns: ignore.")
        else:
            res.notes.append(msg + " (ignored)")
    _check_dates(res, path.name, rows, ids.get("as_of"), cfg["date_formats"])

    units = cfg["_dictionary"].get("sensitivities", {})
    totals = {c: 0.0 for c in risk_cols if mapping[c]}
    bad: list[str] = []
    drop_zero = cfg.get("drop_zero_sensitivities", True)
    for r in rows:
        cp = (r.get(ids["counterparty_id"]) or "").strip()
        ent = ((r.get(ids["legal_entity"]) or "").strip() or None) if ids.get("legal_entity") else None
        if not cp:
            continue
        res.counterparties.setdefault(cp, None)
        for c, m in mapping.items():
            if m is None:
                continue
            v = to_number(r.get(c))
            if v is None:
                if (r.get(c) or "").strip():
                    bad.append(f"{cp}.{c}")
                continue
            totals[c] += v
            if drop_zero and v == 0:
                continue
            ac, rf, bucket, measure = m
            unit = units.get(ac, {}).get(measure, {}).get("unit", "CAD")
            res.sens.append((res.as_of, None, cp, ac, rf, bucket, measure, v, unit, ent, component))
    if bad:
        res.failures.append(f"{path.name}: non-numeric values: {_cut(bad)}")
    defined = cfg["_dictionary"].get("sensitivities", {})
    undefined = sorted({f"{m[0]}/{m[3]}" for m in mapping.values() if m and m[3] not in defined.get(m[0], {})})
    if undefined:
        res.failures.append(f"{path.name}: rules produce measures not in the dictionary: {undefined}")
    return totals


def read_counterparty_credit(res: DayResult, cfg: dict[str, Any], path: Path, spec: dict[str, Any]) -> None:
    """XVA_ALL01: credit spread sensitivities, counterparty attributes and the credit spread curve."""
    header, rows = read_csv(path)
    attrs: dict[str, str] = spec.get("attributes") or {}
    curve_rx = re.compile(spec["curve"]["regex"]) if spec.get("curve") else None
    curve_cols = {c: int(m.group("months")) for c in header if curve_rx and (m := curve_rx.match(c))}
    skip = set(attrs) | set(curve_cols)
    read_sensitivity(res, cfg, path, spec.get("component", "XVA"), spec["id_columns"],
                     compile_rules(spec.get("columns")), "credit_spreads", skip)
    id_col = spec["id_columns"]["counterparty_id"]
    bad: list[str] = []
    for r in rows:
        cp = (r.get(id_col) or "").strip()
        if not cp:
            continue
        found = {field_: (r.get(col) or "").strip() for col, field_ in attrs.items() if (r.get(col) or "").strip()}
        if found:
            res.attributes.setdefault(cp, {}).update(found)
        curve_name = found.get("credit_curve")
        for col, months in curve_cols.items():
            v = to_number(r.get(col))
            if v is None:
                if (r.get(col) or "").strip():
                    bad.append(f"{cp}.{col}")
                continue
            res.curves.append((res.as_of, None, cp, curve_name, months, v))
    if bad:
        res.failures.append(f"{path.name}: non-numeric curve values: {_cut(bad)}")


def check_control(res: DayResult, cfg: dict[str, Any], control: Path, totals: dict[str, float]) -> None:
    header, rows = read_csv(control)
    tol = float(cfg.get("tolerance", 1.0))
    diffs = []
    for c, total in totals.items():
        if c not in header:
            continue
        expected = sum(to_number(r.get(c)) or 0.0 for r in rows)
        if abs(expected - total) > tol:
            diffs.append(f"{c} {total:,.2f} vs {expected:,.2f}")
    if diffs:
        _report(res, cfg, f"{control.name}: column totals differ from the detail file: {_cut(diffs)}")


def read_history(res: DayResult, cfg: dict[str, Any], path: Path) -> None:
    spec, dic = cfg["history"], cfg["_dictionary"]
    header, rows = read_csv(path)
    need = [spec["date_column"], *spec["components"], *( [spec["total_column"]] if spec.get("total_column") else [])]
    missing = [c for c in need if c not in header]
    if missing:
        res.failures.append(f"{path.name}: missing columns {missing}")
        return
    in_total = [c for c in spec["components"] if dic["measures"][c]["in_total"]]
    tol = float(cfg.get("tolerance", 1.0))
    seen, bad_dates, off = set(), [], []
    for r in rows:
        d = parse_date(r.get(spec["date_column"], ""), cfg["date_formats"])
        if d is None or d > res.as_of:
            bad_dates.append(r.get(spec["date_column"], ""))
            continue
        if d in seen:
            bad_dates.append(f"{d} (twice)")
            continue
        seen.add(d)
        vals = {c: to_number(r.get(c)) or 0.0 for c in spec["components"]}
        if spec.get("total_column"):
            reported = to_number(r.get(spec["total_column"]))
            if reported is None or abs(reported - sum(vals[c] for c in in_total)) > tol:
                off.append(str(d))
        res.history += [(d, c, v) for c, v in vals.items()]
    if bad_dates:
        res.failures.append(f"{path.name}: bad or future dates {bad_dates[:3]}")
    if off:
        _report(res, cfg, f"{path.name}: {spec['total_column']} differs from {' + '.join(in_total)} on {len(off)} dates "
                          f"({_cut(off)})")
    res.history_file = path.name

    # the desk total for this date should equal the sum of the counterparty file
    day = {c: v for d, c, v in res.history if d == res.as_of}
    if day and res.values:
        by_cpty: dict[str, float] = {}
        for row in res.values:
            by_cpty[row[4]] = by_cpty.get(row[4], 0.0) + row[5]
        diffs = [f"{c} {by_cpty.get(c, 0.0):,.2f} vs {day[c]:,.2f}" for c in day
                 if abs(by_cpty.get(c, 0.0) - day[c]) > tol]
        if diffs:
            _report(res, cfg, f"Counterparty total differs from desk history for {res.as_of}: {_cut(diffs)}")


def read_day(cfg: dict[str, Any], folder: Path, d: date, values_only: bool = False) -> DayResult:
    res = DayResult(as_of=d)
    vpath = _file(cfg, folder, cfg["values"]["pattern"], d)
    if vpath is None:
        res.failures.append(f"No values file for {d} in {folder}")
        return res
    res.files.append(("values", vpath, _sha(vpath)))
    read_values(res, cfg, vpath)

    if not values_only and cfg.get("sensitivities"):
        spec = cfg["sensitivities"]
        rules = compile_rules(spec.get("columns"))
        col_totals: dict[str, dict[str, float]] = {}
        for f in spec.get("files", []):
            path = _file(cfg, folder, f["pattern"], d)
            if path is None:
                res.notes.append(f"No {f['pattern'].replace('{yyyymmdd}', d.strftime('%Y%m%d'))} for {d}")
                continue
            res.files.append((f"sens_{f['component']}", path, _sha(path)))
            col_totals[f["component"]] = read_sensitivity(res, cfg, path, f["component"], spec["id_columns"], rules,
                                                          "sensitivities")
            control = _file(cfg, folder, f["control"], d) if f.get("control") else None
            if control is not None:
                res.files.append((f"control_{f['component']}", control, _sha(control)))
                check_control(res, cfg, control, col_totals[f["component"]])
        for total, parts in (spec.get("sum_check") or {}).items():
            if total in col_totals and all(p in col_totals for p in parts):
                tol = float(cfg.get("tolerance", 1.0))
                diffs = [c for c, v in col_totals[total].items()
                         if abs(v - sum(col_totals[p].get(c, 0.0) for p in parts)) > tol]
                if diffs:
                    _report(res, cfg, f"{total} sensitivities differ from {' + '.join(parts)} in columns {_cut(diffs)}")

    if not values_only and cfg.get("credit_spreads"):
        spec = cfg["credit_spreads"]
        path = _file(cfg, folder, spec["pattern"], d)
        if path is None:
            res.notes.append(f"No credit spread file for {d}")
        else:
            res.files.append(("credit_spreads", path, _sha(path)))
            read_counterparty_credit(res, cfg, path, spec)

    if cfg.get("ssa"):
        path = _file(cfg, folder, cfg["ssa"]["pattern"], d)
        if path is not None:
            res.files.append(("ssa", path, _sha(path)))
            _, rows = read_csv(path)
            for r in rows:
                cp = (r.get(cfg["ssa"].get("id_column", "CPTY_CODE")) or "").strip()
                if cp:
                    res.counterparties[cp] = "SSA"
                    res.attributes.setdefault(cp, {})["segment"] = "SSA"

    if cfg.get("history"):
        path = _file(cfg, folder, cfg["history"]["pattern"], d)
        if path is not None:
            res.files.append(("history", path, _sha(path)))
            read_history(res, cfg, path)

    digest = hashlib.sha256("".join(sorted(h for _, _, h in res.files)).encode()).hexdigest()
    res.run_id = f"{cfg.get('source', 'xva_system').upper()}-{d:%Y%m%d}-{digest[:8]}"
    return res


# ---------------------------------------------------------------- writing one date


def write_day(cfg: dict[str, Any], store: Path, res: DayResult) -> None:
    source = cfg.get("source", "xva_system")
    now, who = datetime.now(timezone.utc), getpass.getuser()
    with db.connect(store) as con:
        ensure_schema(con)
        previous = con.execute(
            "SELECT run_id, checks_passed FROM data_status WHERE as_of = ? AND source = ?", [res.as_of, source]
        ).fetchone()
        known = {r[0]: r[1] for r in con.execute("SELECT counterparty_id, sector FROM dim_counterparty").fetchall()}
        new_cps = sorted((set(res.counterparties) | set(res.attributes)) - set(known))
        if new_cps and res.loaded:
            res.notes.append(f"{len(new_cps)} new counterparties added: {_cut(new_cps)}")

        with con.transaction():
            con.execute("DELETE FROM xva_value WHERE as_of = ? AND run_id = ?", [res.as_of, res.run_id])
            con.execute("DELETE FROM xva_sensitivity WHERE as_of = ? AND run_id = ?", [res.as_of, res.run_id])
            if res.loaded:
                con.bulk_insert("xva_value", VALUE_COLS, [(r[0], res.run_id, *r[2:]) for r in res.values])
                con.bulk_insert("xva_sensitivity", SENS_COLS, [(r[0], res.run_id, *r[2:]) for r in res.sens])
                con.bulk_insert(
                    "dim_counterparty",
                    ["counterparty_id", "name", "desk"],
                    [(cp, cp, cfg.get("default_desk", "XVA Desk")) for cp in new_cps],
                )
                # attributes from this date's files, unless a later date has already set them
                for cp, attrs in res.attributes.items():
                    cols = [c for c in ATTRIBUTE_COLS if c in attrs]
                    if cols:
                        con.execute(
                            f"UPDATE dim_counterparty SET {', '.join(f'{c} = ?' for c in cols)}, attributes_as_of = ? "
                            "WHERE counterparty_id = ? AND (attributes_as_of IS NULL OR attributes_as_of <= ?)",
                            [*(attrs[c] for c in cols), res.as_of, cp, res.as_of],
                        )
                con.execute("DELETE FROM xva_cpty_curve WHERE as_of = ?", [res.as_of])
                con.bulk_insert(
                    "xva_cpty_curve",
                    ["as_of", "run_id", "counterparty_id", "credit_curve", "tenor_months", "spread"],
                    [(r[0], res.run_id, *r[2:]) for r in res.curves],
                )
                if res.history:
                    dates = sorted({h[0] for h in res.history})
                    con.execute("DELETE FROM xva_desk_total WHERE as_of BETWEEN ? AND ?", [dates[0], dates[-1]])
                    con.bulk_insert(
                        "xva_desk_total",
                        ["as_of", "measure", "value", "run_id", "source_file", "loaded_at"],
                        [(d, m, v, res.run_id, res.history_file, now) for d, m, v in res.history],
                    )
                con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [res.as_of, source])
                con.execute(
                    "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
                    [res.as_of, source, res.run_id, now, True, "; ".join(res.notes) or None],
                )
            elif previous and previous[1] and previous[0] != res.run_id:
                con.execute(
                    "UPDATE data_status SET notes = ? WHERE as_of = ? AND source = ?",
                    [f"Restatement {res.run_id} rejected at {now:%Y-%m-%d %H:%M} UTC: " + "; ".join(res.failures),
                     res.as_of, source],
                )
            else:
                con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [res.as_of, source])
                con.execute(
                    "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
                    [res.as_of, source, res.run_id, now, False, "; ".join(res.failures)],
                )
            con.executemany(
                "INSERT INTO landing_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(now, res.as_of, source, res.run_id, ds, p.name, h,
                  (len(res.values) if ds == "values" else 0) if res.loaded else 0, who,
                  "loaded" if res.loaded else "rejected")
                 for ds, p, h in res.files],
            )


def archive_files(res: DayResult, root: Path) -> None:
    folder = root / res.as_of.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for _, p, h in res.files:
        target = folder / f"{p.stem}.{h[:12]}{p.suffix}"
        if not target.exists():
            shutil.copy2(p, target)


def loaded_dates(store: Path, source: str) -> dict[date, tuple[str, bool]]:
    if not store.exists():
        return {}
    with db.connect(store, read_only=True) as con:
        if not db.table_exists(con, "data_status"):
            return {}
        rows = con.execute("SELECT as_of, run_id, checks_passed FROM data_status WHERE source = ?", [source]).fetchall()
    return {r[0]: (r[1], bool(r[2])) for r in rows}


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Load the desk's daily XVA CSV files into the curated store")
    p.add_argument("--config", required=True, help="landing\\desk_csv.yaml")
    p.add_argument("--store", required=True, help="store file (.duckdb recommended)")
    p.add_argument("--folder", help="override the folder in the config")
    p.add_argument("--archive", help="folder for a copy of every input file")
    p.add_argument("--values-only", action="store_true", help="load values only (when sensitivity files are broken)")
    when = p.add_mutually_exclusive_group(required=True)
    when.add_argument("--as-of", type=date.fromisoformat, help="one business date")
    when.add_argument("--from", dest="date_from", type=date.fromisoformat, help="first date of a range (with --to)")
    when.add_argument("--all", action="store_true", help="every date in the folder not loaded yet")
    when.add_argument("--list", action="store_true", help="show the dates in the folder and their load status")
    p.add_argument("--to", dest="date_to", type=date.fromisoformat, help="last date of a range")
    p.add_argument("--reload", action="store_true", help="with --all or a range: also reload dates already loaded")
    a = p.parse_args(argv)

    try:
        cfg = load_config(a.config)
        folder = Path(a.folder or cfg["folder"])
        if not folder.exists():
            raise SetupError(f"Folder not found or not reachable: {folder}")
        store = Path(a.store)
        source = cfg.get("source", "xva_system")
        found = available_dates(cfg, folder)
        done = loaded_dates(store, source)

        if a.list:
            print(f"{len(found)} dates in {folder}")
            for d in found:
                state = done.get(d)
                print(f"  {d}  " + ("not loaded" if not state else ("loaded " if state[1] else "REJECTED ") + state[0]))
            return 0
        if a.as_of:
            dates = [a.as_of]
        elif a.all:
            dates = [d for d in found if a.reload or not (d in done and done[d][1])]
        else:
            end = a.date_to or a.date_from
            dates = [d for d in found if a.date_from <= d <= end and (a.reload or not (d in done and done[d][1]))]
        if not dates:
            print("Nothing to load.")
            return 0
        if not store.exists():
            from .schema import create_store

            create_store(store)
    except (SetupError, OSError, KeyError, yaml.YAMLError) as exc:
        print(f"Setup problem: {exc}", file=sys.stderr)
        return 2

    rejected = 0
    for d in dates:
        res = read_day(cfg, folder, d, a.values_only)
        write_day(cfg, store, res)
        if a.archive:
            archive_files(res, Path(a.archive))
        if res.loaded:
            print(f"{d} run {res.run_id}: loaded ({len(res.values):,} values, {len(res.sens):,} sensitivities)")
            for n in res.notes:
                print(f"    note: {n}")
        else:
            rejected += 1
            print(f"{d} run {res.run_id}: REJECTED, nothing loaded")
            for f in res.failures:
                print(f"    {f}")
    print(f"Done: {len(dates) - rejected} loaded, {rejected} rejected. Tools must use XVA_DICTIONARY_PATH="
          f"{cfg['_dictionary_path']}")
    return 0 if not rejected else 1


if __name__ == "__main__":
    raise SystemExit(main())
