"""Stored procedures: the runner, and loading P&L explain from procedures or from CSV files."""

import csv
import shutil
from datetime import date
from pathlib import Path

import pytest

import fake_sp
from sample_data.make_desk_files import PNL_COLUMNS, build, pnl_table
from xva_landing import load_desk_csv as ldc
from xva_landing import sp_runner as spr
from xva_mcp import db, render, store, tools

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "landing" / "desk_csv.yaml"
D1, D2 = date(2026, 11, 23), date(2026, 11, 24)


# ---------------------------------------------------------------- runner


def test_registry_has_the_notebook_procedures():
    reg = spr.registry_from_config(CONFIG)
    procs = reg["procedures"]
    assert procs["fva_pnl"].sp == "APP_PNL_FVA2_REPORT.SP_GET_CptyFVAPnl_TimeSeries" and procs["fva_pnl"].db == "CVA@CMDB"
    assert {procs[n].params["P_XVAType"] for n in ("cva_pnl", "fva_pnl", "xva_pnl")} == {"CVA", "FVA", "XVA"}
    assert procs["fva01"].sp == "APP_CR01_REPORT.SP_GET_LITES_FVA01_TimeSeries"
    assert reg["backend"] == "mfl" and reg["calendar"] == "TOR"


def test_parameters_are_typed_like_the_notebook():
    proc = spr.load_registry(ROOT / "landing" / "stored_procs.yaml")["procedures"]["fva_pnl"]
    params = spr.render_params(proc, D2, D2)
    assert params["p_PERIODSTART"] == D2 and params["p_PERIODEND"] == D2
    assert params["P_CPTY_CODE"] is None and params["P_IsBMOFG"] is True and params["P_ShowTotalOnly"] is False
    assert params["P_ExcludedCpty"] == ["XVATEST", "CMLEASE"]
    assert spr.render_params(proc, D2, D2, cpty_codes=["100IN", "1SEBK"])["P_CPTY_CODE"] == "100IN,1SEBK"
    assert list(params) == list(proc.params)  # same order as the notebook's dict


def test_unsafe_or_unknown_definitions_are_refused(tmp_path):
    bad = tmp_path / "procs.yaml"
    bad.write_text("procedures:\n  x: {db: 'CVA@CMDB', sp: 'APP.SP; DROP TABLE t', params: {}}\n", encoding="utf-8")
    with pytest.raises(spr.ProcedureError, match="unsafe sp"):
        spr.load_registry(bad)
    proc = spr.Procedure("p", "CVA@CMDB", "APP.SP_X", {"a": "{tomorrow}"})
    with pytest.raises(spr.ProcedureError, match="unknown placeholder"):
        spr.render_params(proc, D2, D2)


def test_results_become_text_tables():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"COB_DATE": [pd.Timestamp("2026-11-24")], "CPTY_CODE": ["100IN"], "PNL": [float("nan")]})
    assert spr.to_table(df) == (["COB_DATE", "CPTY_CODE", "PNL"], [["2026-11-24 00:00:00", "100IN", ""]])
    assert spr.to_table([{"A": 1, "B": None}]) == (["A", "B"], [["1", ""]])
    assert spr.to_table({"A": [D2], "B": [2.5]}) == (["A", "B"], [["2026-11-24", "2.5"]])
    with pytest.raises(spr.ProcedureError):
        spr.to_table(42)


def test_procedure_errors_are_reported_not_raised_raw():
    proc = spr.Procedure("x", "CVA@CMDB", "APP.SP_GET_CptyFVAPnl_TimeSeries",
                         {"p_PERIODSTART": "{start}", "p_PERIODEND": "{end}", "P_XVAType": "CVA"})
    fake_sp.FAIL.add("CVA")
    try:
        with pytest.raises(spr.ProcedureError, match="ORA-20001"):
            spr.run_procedure(fake_sp.FakeBackend(), proc, D2, D2)
    finally:
        fake_sp.FAIL.clear()


def test_business_day_dates():
    monday = date(2026, 11, 30)
    assert spr.resolve_date("t-1", fake_sp.FakeBackend(), today=monday) == date(2026, 11, 27)
    assert spr.resolve_date("t-2", None, today=monday) == date(2026, 11, 26)
    assert spr.resolve_date("2026-11-24", None) == D2
    with pytest.raises(spr.ProcedureError, match="MFL"):  # no pymfl here
        spr.MflBackend().run("CVA@CMDB", "APP.SP", {})


# ---------------------------------------------------------------- loading P&L explain


@pytest.fixture
def desk(tmp_path, monkeypatch):
    folder = build(tmp_path / "csv", [D1, D2])
    fake_sp.VALUES_FOLDER = folder
    fake_sp.LAYOUT = "wide"
    store_path = tmp_path / "store" / "xva_store.duckdb"
    if not db.duckdb_available():
        store_path = store_path.with_suffix(".sqlite")
    monkeypatch.setenv("XVA_DICTIONARY_PATH", str(ROOT / "landing" / "desk_dictionary.yaml"))
    monkeypatch.setenv("XVA_STORE_PATH", str(store_path))
    store.dictionary.cache_clear()
    cfg = ldc.load_config(CONFIG)
    cfg["sp_output_folder"] = str(tmp_path / "sp_output")
    yield cfg, folder, store_path, tmp_path
    store.dictionary.cache_clear()
    fake_sp.FAIL.clear()
    fake_sp.LAYOUT = "wide"


def run_days(cfg, folder, store_path, overrides=None, dates=(D1, D2)):
    fx = ldc.Fetcher(cfg, folder, overrides, backend=fake_sp.FakeBackend())
    results = []
    for d in dates:
        res = ldc.read_day(cfg, folder, d, fetcher=fx)
        ldc.write_day(cfg, store_path, res)
        results.append(res)
    return results


def expected_from_generator(d, component, column):
    header, rows = pnl_table(d, component)
    i = header.index(column)
    return sum(float(r[i]) for r in rows if r[i] != "")


def test_pnl_from_stored_procedures(desk):
    cfg, folder, store_path, tmp = desk
    res = run_days(cfg, folder, store_path)
    assert all(r.loaded for r in res) and res[-1].pnl and res[-1].pnl_actual
    # the real layout ties out and every column is a known driver
    assert not [n for n in res[-1].notes if "not the sum" in n or "not in the dictionary" in n or "not loaded" in n]
    # each procedure's result was saved as CSV for audit, one folder per date
    saved = sorted(p.name for p in (tmp / "sp_output" / "20261124").iterdir())
    assert saved == ["XVA_PnL_CVA_20261124.csv", "XVA_PnL_FVA_20261124.csv", "XVA_PnL_XVA_20261124.csv"]
    assert all(r[5] != 0 for r in res[-1].pnl)  # zeros are not stored

    total = tools.call("get_pnl_attribution", {"start": D1, "end": D2})
    assert total.component == "XVA" and total.reconciliation_basis == "actual_pl"
    assert total.actual_pl == pytest.approx(expected_from_generator(D2, "XVA", "ACTUAL_PL"))
    assert total.expected_pl == pytest.approx(expected_from_generator(D2, "XVA", "Total Expected P/L"))
    assert abs(total.reconciliation_gap) < 0.01
    drivers = {d.driver: d for d in total.drivers}
    assert drivers["USD_RATES"].group == "Interest rates" and drivers["unexplained"].group == "Unexplained"
    assert drivers["USD_RATES"].value == pytest.approx(expected_from_generator(D2, "XVA", "USD_RATES"))
    assert "EUR_RATES" not in drivers  # all zero
    groups = [g.driver for g in total.groups]
    assert groups[:2] == ["Counterparty credit", "BMO credit"] and groups[-1] == "Unexplained"
    assert sum(g.value for g in total.groups) == pytest.approx(sum(d.value for d in total.drivers))
    order = [d.driver for d in total.drivers]
    assert order.index("CPTY_CREDIT_NOCLOSEOUT") < order.index("USD_RATES") < order.index("unexplained")

    # XVA = CVA + FVA driver by driver, and the components keep their own actual P&L
    cva = tools.call("get_pnl_attribution", {"start": D1, "end": D2, "component": "CVA"})
    fva = tools.call("get_pnl_attribution", {"start": D1, "end": D2, "component": "FVA"})
    assert cva.actual_pl + fva.actual_pl == pytest.approx(total.actual_pl)
    assert "BMO_FUNDING_ASSET" in {d.driver for d in fva.drivers} - {d.driver for d in cva.drivers}
    ent = tools.call("get_pnl_attribution", {"start": D1, "end": D2, "level": "entity", "key": "BMO"})
    assert ent.actual_pl == pytest.approx(total.actual_pl)  # all P&L counterparties are BMO entity here
    cp = tools.call("get_pnl_attribution", {"start": D1, "end": D2, "level": "counterparty", "key": "100IN"})
    assert abs(cp.reconciliation_gap) < 0.01 and abs(cp.actual_pl) < abs(total.actual_pl)
    with pytest.raises(tools.DataMissing, match="stored for XVA, CVA, FVA"):
        tools.call("get_pnl_attribution", {"start": D1, "end": D2, "component": "DVA"})
    text = render.render(total)
    assert "By driver group" in text and "Actual P&L (P&L system)" in text and "actual P&L minus drivers" in text


def test_the_desks_example_ties_out(desk):
    """The two rows from the desk's screenshot of the procedure output (counterparty codes replaced)."""
    cfg, folder, store_path, _ = desk
    blank = {"CPTY_CREDIT_ON_FVA", "BMO_CREDIT_ON_FVA", "BMO_FUNDING_ASSET", "BMO_FUNDING_LIABLITY", "JPY_RATES",
             "UNEXPLAINED_COMMODITIES", "DOCUMENTATION", "JOINT_DEFAULT_CORRELATION", "MODEL_CHANGES",
             "CALIBRATION", "UNWIND_TRADES", "TEMPORARY_TRADEEFFECTS", "TEMPORARY_MAPPINGISSUES",
             "TEMPORARY_TRAPPEDCOLLATERAL"}
    shown = [
        {"ACTUAL_PL": -129477.4, "CPTY_CREDIT_NOCLOSEOUT": -1691.528, "BMO_CREDIT_NOCLOSEOUT": 53.13057,
         "USD_RATES": -16325.08, "USD_IR_VEGA": 767.6709, "USDCAD_DELTA": 739.1045, "CARRY": -321.645,
         "NEW_TRADES": -111999, "Total Expected P/L": -128777, "Unexplained P/L": -700.272},
        {"ACTUAL_PL": 38075.51, "CPTY_CREDIT_NOCLOSEOUT": 8815.937, "BMO_CREDIT_NOCLOSEOUT": 20.96139,
         "CAD_RATES": 566.747, "USD_RATES": -2005.295, "CORRASOFR_RATES": 1.890293, "USD_IR_VEGA": 0.644026,
         "CAD_IR_VEGA": -0.00534, "USDCAD_DELTA": 10161.16, "USDCAD_GAMMA": -164.296, "USDCAD_VEGA": -474.072,
         "CARRY": 1.19889, "NEW_TRADES": 20710.07, "Total Expected P/L": 37634.93, "Unexplained P/L": 440.5715},
    ]
    rows = []
    for code, vals in zip(["CPTY1", "CPTY2"], shown):
        row = {c: ("" if c in blank else vals.get(c, 0)) for c in PNL_COLUMNS}
        row.update({"COB_DATE": "2026-11-24 00:00:00", "INTERNAL_ENTITY": "BMO", "CPTY_CODE": code})
        rows.append(row)
    path = folder / "example.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, PNL_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    res = ldc.DayResult(as_of=D2)
    ldc.read_pnl(res, cfg, path, "CVA", cfg["pnl_explain"])
    assert not res.failures and not [n for n in res.notes if "not the sum" in n or "not in the dictionary" in n]
    actual = {r[2]: (r[4], r[5]) for r in res.pnl_actual}
    assert actual == {"CPTY1": (-129477.4, -128777), "CPTY2": (38075.51, 37634.93)}
    stored = {(r[2], r[4]): r[5] for r in res.pnl}
    assert stored[("CPTY1", "unexplained")] == -700.272 and ("CPTY1", "EUR_RATES") not in stored
    assert len([k for k in stored if k[0] == "CPTY2"]) == 13


def test_tieout_breaks_are_reported(desk):
    cfg, folder, store_path, _ = desk
    path = folder / "XVA_PnL_CVA_20261124.csv"
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    i = rows[0].index("Total Expected P/L")
    rows[1][i] = str(float(rows[1][i]) + 500)  # expected no longer the sum of its drivers, nor actual
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)
    res = run_days(cfg, folder, store_path, overrides={"pnl_explain": "csv"}, dates=(D2,))[0]
    assert res.loaded and res.pnl
    assert any("Total Expected P/L is not the sum of the drivers it covers on 1 of" in n and "500.00" in n
               for n in res.notes)
    cfg["pnl_explain"]["strict_tieouts"] = True
    res = run_days(cfg, folder, store_path, overrides={"pnl_explain": "csv"}, dates=(D2,))[0]
    assert res.loaded and not res.pnl and any("P&L explain not loaded" in n for n in res.notes)


def test_csv_and_sp_give_the_same_answer(desk, monkeypatch):
    cfg, folder, store_path, tmp = desk
    run_days(cfg, folder, store_path)
    from_sp = tools.call("get_pnl_attribution", {"start": D1, "end": D2})

    # put the saved procedure outputs in the CSV folder and load a fresh store from CSV only
    for day in ("20261123", "20261124"):
        for f in (tmp / "sp_output" / day).iterdir():
            shutil.copy(f, folder / f.name)
    csv_store = store_path.with_name("from_csv" + store_path.suffix)
    run_days(cfg, folder, csv_store, overrides={"all": "csv"})
    monkeypatch.setenv("XVA_STORE_PATH", str(csv_store))
    from_csv = tools.call("get_pnl_attribution", {"start": D1, "end": D2})
    assert [(d.driver, round(d.value, 6)) for d in from_csv.drivers] == \
           [(d.driver, round(d.value, 6)) for d in from_sp.drivers]
    assert from_csv.actual_pl == pytest.approx(from_sp.actual_pl)


def test_sample_folder_pnl_files_load_from_csv(desk, monkeypatch):
    cfg, folder, store_path, _ = desk
    res = run_days(cfg, folder, store_path, overrides={"pnl_explain": "csv"})
    assert all(r.loaded and r.pnl for r in res)
    out = tools.call("get_pnl_attribution", {"start": D1, "end": D2})
    assert out.actual_pl == pytest.approx(expected_from_generator(D2, "XVA", "ACTUAL_PL"))


def test_procedure_failure_is_a_note_unless_required(desk):
    cfg, folder, store_path, _ = desk
    fake_sp.FAIL.add("FVA")
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert res.loaded and not res.pnl and not res.pnl_actual  # values still loaded, P&L skipped as a whole
    assert any("P&L explain not loaded" in n and "ORA-20001" in n for n in res.notes)

    cfg["pnl_explain"]["required"] = True
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert not res.loaded and any("ORA-20001" in f for f in res.failures)


def test_unknown_drivers_keep_or_fail(desk):
    cfg, folder, store_path, _ = desk
    cfg["pnl_explain"]["driver_map"] = {}
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert res.loaded and any("Unexplained P/L" in n and "not in the dictionary" in n for n in res.notes)
    assert "Unexplained P/L" in {r[4] for r in res.pnl}
    cfg["pnl_explain"]["unknown_drivers"] = "fail"
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert res.loaded and not res.pnl and any("P&L explain not loaded" in n for n in res.notes)


def test_long_layout_and_explicit_columns(desk):
    cfg, folder, store_path, _ = desk
    wide = run_days(cfg, folder, store_path, dates=(D2,))[0]
    totals = {}
    for r in wide.pnl:
        totals[r[3]] = totals.get(r[3], 0.0) + r[5]

    fake_sp.LAYOUT = "long"
    long_spec = {k: v for k, v in cfg["pnl_explain"].items()
                 if k not in ("driver_columns", "actual_column", "expected_column")}
    cfg["pnl_explain"] = {**long_spec, "driver_column": "PNL_DRIVER", "value_column": "PNL"}
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert res.loaded and not res.pnl_actual
    long_totals = {}
    for r in res.pnl:
        long_totals[r[3]] = long_totals.get(r[3], 0.0) + r[5]
    assert long_totals == pytest.approx(totals)

    fake_sp.LAYOUT = "wide"
    cfg["pnl_explain"] = {**long_spec, "driver_columns": {"USD_RATES": "USD_RATES", "Unexplained P/L": "unexplained"}}
    cfg["pnl_explain"].pop("not_in_expected")
    res = run_days(cfg, folder, store_path, dates=(D2,))[0]
    assert res.loaded and {r[4] for r in res.pnl} == {"USD_RATES", "unexplained"}

    res = run_days(cfg, folder, store_path, overrides={"pnl_explain": "off"}, dates=(D2,))[0]
    assert res.loaded and not res.pnl


def test_command_line_with_a_python_backend(desk, tmp_path, capsys):
    cfg, folder, store_path, tmp = desk
    reg = tmp / "procs.yaml"
    reg.write_text((ROOT / "landing" / "stored_procs.yaml").read_text(encoding="utf-8")
                   .replace("backend: mfl", "backend: python\ncallable: fake_sp:run_sp"), encoding="utf-8")
    conf = tmp / "desk_csv.yaml"
    conf.write_text(CONFIG.read_text(encoding="utf-8")
                    .replace("stored_procs: stored_procs.yaml", f"stored_procs: '{reg}'")
                    .replace("mapping: variants.yaml", f"mapping: '{ROOT / 'landing' / 'variants.yaml'}'")
                    .replace("dictionary: desk_dictionary.yaml", f"dictionary: '{ROOT / 'landing' / 'desk_dictionary.yaml'}'")
                    .replace("sp_output_folder: '%USERPROFILE%\\xva_data\\sp_output'", f"sp_output_folder: '{tmp / 'spo'}'"),
                    encoding="utf-8")
    args = ["--config", str(conf), "--store", str(store_path), "--folder", str(folder)]
    assert ldc.main(args + ["--as-of", "2026-11-24"]) == 0
    out = capsys.readouterr().out
    assert "pnl_explain=sp" in out and "P&L rows" in out and (tmp / "spo" / "20261124").exists()
    assert ldc.main(args + ["--as-of", "2026-11-24", "--source", "pnl_explain=off"]) == 0
    assert ldc.main(args + ["--as-of", "2026-11-24", "--source", "pnl=sp"]) == 2

    # the inspect command shows a procedure's columns
    assert spr.main(["--config", str(conf), "--proc", "fva_pnl", "--as-of", "2026-11-24", "--show", "2"]) == 0
    out = capsys.readouterr().out
    assert "Columns: COB_DATE, INTERNAL_ENTITY, CPTY_CODE, ACTUAL_PL, CPTY_CREDIT_NOCLOSEOUT" in out
    assert spr.main(["--config", str(conf), "--list"]) == 0 and "fva01" in capsys.readouterr().out
    assert spr.main(["--config", str(conf), "--proc", "nope", "--as-of", "2026-11-24"]) == 2
