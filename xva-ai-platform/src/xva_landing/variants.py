"""Counterparty variants: test calculations of a counterparty (e.g. XVATEST-CHT and XVATEST-CHT2 for CHT),
loaded next to the official data so the desk can compare them, but never inside it.

- landing/variants.yaml lists each variant: its code, the official counterparty it belongs to (base), what it
  calculates differently (method) and who owns it.
- The variants section of desk_csv.yaml says which datasets to load for them (values, sensitivities,
  credit_spreads, pnl_explain) and how: through the same stored procedures with P_CPTY_CODE = the variant codes and the
  parameters in `params` replaced (P_ExcludedCpty without XVATEST), or from CSV files named with file_prefix.
- Rows go to separate tables (xva_variant_value, xva_variant_sensitivity, xva_variant_pnl_explain,
  xva_variant_pnl_actual) that no official query reads. Official loads drop any XVATEST row they meet.
- A date's variant load is recorded in data_status under source xva_variants; a problem there is a note and
  never affects the official load.

Called by load_desk_csv after each official day, or alone with --variants-only. --find-variants lists XVATEST
codes a procedure returns that variants.yaml doesn't list yet.
"""

from __future__ import annotations

import getpass
import hashlib
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from xva_mcp import db

from .load_desk_csv import (
    PNL_ACTUAL_COLS,
    PNL_COLS,
    SENS_COLS,
    VALUE_COLS,
    DayResult,
    Fetcher,
    SetupError,
    _cut,
    _sha,
    compile_rules,
    is_variant,
    read_counterparty_credit,
    read_pnl,
    read_sensitivity,
    read_values,
    variant_marker,
)
from .schema import ensure_schema

VARIANT_SOURCE = "xva_variants"
VARIANT_DATASETS = ("values", "sensitivities", "credit_spreads", "pnl_explain")
TABLES = {
    "values": ("xva_variant_value", VALUE_COLS),
    "sens": ("xva_variant_sensitivity", SENS_COLS),
    "pnl": ("xva_variant_pnl_explain", PNL_COLS),
    "pnl_actual": ("xva_variant_pnl_actual", PNL_ACTUAL_COLS),
}


def variant_spec(cfg: dict[str, Any]) -> dict[str, Any] | None:
    return cfg.get("variants") or None


def load_mapping(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """variants.yaml -> [{code, base, method, owner}], checked: every code carries the marker, appears once,
    and has a base that is not itself a variant."""
    spec = variant_spec(cfg) or {}
    path = Path(os.path.expandvars(str(spec.get("mapping", "variants.yaml"))))
    if not path.is_absolute():
        path = Path(cfg["_config_dir"]) / path
    if not path.exists():
        raise SetupError(f"variants mapping file not found: {path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    marker = variant_marker(cfg)
    out, seen = [], set()
    for i, item in enumerate(doc.get("variants") or [], 1):
        code = str((item or {}).get("code") or "").strip()
        base = str((item or {}).get("base") or "").strip()
        if not code or not base:
            raise SetupError(f"{path.name}: entry {i} needs both code and base")
        if not is_variant(code, marker):
            raise SetupError(f"{path.name}: {code} does not contain {marker}; only test variants belong here")
        if is_variant(base, marker):
            raise SetupError(f"{path.name}: base {base} of {code} is itself a variant")
        if code.upper() in seen:
            raise SetupError(f"{path.name}: {code} is listed twice")
        seen.add(code.upper())
        out.append({"code": code, "base": base, "method": str(item.get("method") or "").strip(),
                    "owner": str(item.get("owner") or "").strip()})
    return out


def _merge(into: DayResult, part: DayResult) -> None:
    into.files += part.files
    into.values += part.values
    into.sens += part.sens
    into.pnl += part.pnl
    into.pnl_actual += part.pnl_actual
    into.notes += part.notes


def _dataset(vres: DayResult, label: str, read: Any) -> None:
    """Read one dataset into its own result; a problem is a note and the other datasets still load."""
    part = DayResult(as_of=vres.as_of)
    try:
        read(part)
    except SetupError as exc:
        part.failures.append(str(exc))
    if part.failures:
        vres.notes.append(f"variant {label} not loaded: " + "; ".join(part.failures))
        vres.files += part.files
    else:
        _merge(vres, part)


def read_variants(cfg: dict[str, Any], folder: Path, d: date, fx: Fetcher,
                  codes: list[str] | None = None, file_prefix: str | None = None) -> DayResult:
    """Variant rows for one date. codes=None asks the procedures for every counterparty (used by
    --find-variants); otherwise only the codes in variants.yaml are requested and kept."""
    spec = variant_spec(cfg) or {}
    marker = variant_marker(cfg)
    vres = DayResult(as_of=d)
    vfx = fx.for_variants({**spec, "file_prefix": file_prefix} if file_prefix else spec, codes)
    mode = vfx.source("values")
    datasets = spec.get("datasets") or list(VARIANT_DATASETS)
    unknown = [x for x in datasets if x not in VARIANT_DATASETS]
    if unknown:
        raise SetupError(f"variants: datasets can be {', '.join(VARIANT_DATASETS)}; not {unknown}")

    def needs_procedure(entries: list[dict[str, Any]], what: str) -> bool:
        if mode == "sp" and not all(e.get("procedure") for e in entries):
            vres.notes.append(f"variant {what} skipped: {what} has no procedure in desk_csv.yaml yet")
            return True
        return False

    def fetch(part: DayResult, dataset: str, entry: dict[str, Any], label: str) -> Path | None:
        path = vfx.get(part, dataset, entry, d, label)
        if path is None:
            errors = [e for e in part.sp_errors if e.startswith(f"{label}:")]
            part.failures.append(errors[-1] if errors else f"no {label} input for {d}")
            return None
        part.files.append((label, path, _sha(path)))
        return path

    if "values" in datasets and cfg.get("values") and not needs_procedure([cfg["values"]], "values"):
        def values(part: DayResult) -> None:
            path = fetch(part, "values", cfg["values"], "variant_values")
            if path is not None:
                read_values(part, cfg, path)
        _dataset(vres, "values", values)

    sens = cfg.get("sensitivities")
    if "sensitivities" in datasets and sens and not needs_procedure(sens.get("files", []), "sensitivities"):
        rules = compile_rules(sens.get("columns"))
        for f in sens.get("files", []):
            def one_sens(part: DayResult, f: dict[str, Any] = f) -> None:
                path = fetch(part, "sensitivities", f, f"variant_sens_{f['component']}")
                if path is not None:
                    read_sensitivity(part, cfg, path, f["component"], sens["id_columns"], rules, "sensitivities")
            _dataset(vres, f"sensitivities {f['component']}", one_sens)

    cs = cfg.get("credit_spreads")
    if "credit_spreads" in datasets and cs and not needs_procedure([cs], "credit_spreads"):
        def credit(part: DayResult) -> None:  # the CR sensitivities only; attributes and curves stay official
            path = fetch(part, "credit_spreads", cs, "variant_credit_spreads")
            if path is not None:
                read_counterparty_credit(part, cfg, path, cs)
        _dataset(vres, "credit spreads", credit)

    pnl = cfg.get("pnl_explain")
    if "pnl_explain" in datasets and pnl and not needs_procedure(pnl.get("files", []), "pnl_explain"):
        for f in pnl.get("files", []):
            def one_pnl(part: DayResult, f: dict[str, Any] = f) -> None:
                path = fetch(part, "pnl_explain", f, f"variant_pnl_{f['component']}")
                if path is not None:
                    read_pnl(part, cfg, path, f["component"], pnl)
            _dataset(vres, f"P&L {f['component']}", one_pnl)

    # keep variant rows only: the listed codes, or (codes=None) anything carrying the marker
    wanted = {c.upper() for c in codes} if codes is not None else None
    found = {r[2] for name in TABLES for r in getattr(vres, name)}
    keep = {cp for cp in found if is_variant(cp, marker) and (wanted is None or cp.upper() in wanted)}
    dropped = found - keep
    if dropped:
        vres.notes.append(f"{len(dropped)} counterparties returned that are not listed variants were left out: "
                          f"{_cut(sorted(dropped))}")
    for name in TABLES:
        setattr(vres, name, [r for r in getattr(vres, name) if r[2] in keep])
    vres.counterparties = {cp: None for cp in keep}
    if codes:
        absent = sorted(set(codes) - keep)
        if absent:
            vres.notes.append(f"no rows for {len(absent)} listed variants: {_cut(absent)}")
    digest = hashlib.sha256("".join(sorted(h for _, _, h in vres.files)).encode()).hexdigest()
    vres.run_id = f"XVA_VARIANTS-{d:%Y%m%d}-{digest[:8]}"
    if not keep:
        vres.failures.append("no variant rows loaded" + (": " + "; ".join(vres.notes) if vres.notes else ""))
    return vres


def write_variants(store: Path, vres: DayResult, mapping: list[dict[str, str]]) -> None:
    """Replace this date's variant rows and the variant list; record the load in data_status and landing_log."""
    now, who = datetime.now(timezone.utc), getpass.getuser()
    store.parent.mkdir(parents=True, exist_ok=True)
    with db.connect(store) as con:
        ensure_schema(con)
        official = {r[0] for r in con.execute("SELECT counterparty_id FROM dim_counterparty").fetchall()}
        missing_bases = sorted({m["base"] for m in mapping} - official)
        if missing_bases:
            vres.notes.append(f"base counterparties not in the official data yet: {_cut(missing_bases)}")
        with con.transaction():
            con.execute("DELETE FROM dim_counterparty_variant")
            con.bulk_insert("dim_counterparty_variant", ["variant_id", "base_id", "method", "owner", "updated_at"],
                            [(m["code"], m["base"], m["method"] or None, m["owner"] or None, now) for m in mapping])
            for table, _ in TABLES.values():
                con.execute(f"DELETE FROM {table} WHERE as_of = ?", [vres.as_of])
            if vres.loaded:
                for name, (table, cols) in TABLES.items():
                    con.bulk_insert(table, cols, [(r[0], vres.run_id, *r[2:]) for r in getattr(vres, name)])
            con.execute("DELETE FROM data_status WHERE as_of = ? AND source = ?", [vres.as_of, VARIANT_SOURCE])
            con.execute(
                "INSERT INTO data_status VALUES (?, ?, ?, ?, ?, ?)",
                [vres.as_of, VARIANT_SOURCE, vres.run_id, now, vres.loaded,
                 "; ".join(vres.notes if vres.loaded else vres.failures) or None],
            )
            log = [(now, vres.as_of, VARIANT_SOURCE, vres.run_id, ds, p.name, h, 0, who,
                    "loaded" if vres.loaded else "rejected") for ds, p, h in vres.files]
            if log:
                con.executemany("INSERT INTO landing_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", log)


def summary_line(vres: DayResult) -> str:
    if not vres.loaded:
        return f"variants: nothing loaded ({'; '.join(vres.failures)})"
    return (f"variants: {len(vres.counterparties)} loaded ({len(vres.values):,} values, {len(vres.sens):,} "
            f"sensitivities, {len(vres.pnl):,} P&L rows)")


def find_unlisted(cfg: dict[str, Any], folder: Path, d: date, fx: Fetcher) -> tuple[list[str], list[str]]:
    """(codes returned with the marker that variants.yaml doesn't list, notes)."""
    listed = {m["code"].upper() for m in load_mapping(cfg)}
    spec = variant_spec(cfg) or {}
    prefix = None if fx.for_variants(spec, None).source("values") == "csv" else "VARIANTS_ALL_"
    vres = read_variants(cfg, folder, d, fx, codes=None, file_prefix=prefix)
    return sorted(cp for cp in vres.counterparties if cp.upper() not in listed), vres.notes
