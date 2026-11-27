"""The desk CSV loader, on fictional files laid out like the desk's XVA CSV folder."""

import csv
from datetime import date
from pathlib import Path

import pytest

from sample_data.make_desk_files import build
from xva_landing import load_desk_csv as ldc
from xva_mcp import store, tools

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "landing" / "desk_csv.yaml"
D1, D2 = date(2026, 11, 23), date(2026, 11, 24)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    folder = build(tmp_path / "csv", [D1, D2])
    store_path = tmp_path / "store" / "xva_store.duckdb"
    if not __import__("xva_mcp.db", fromlist=["db"]).duckdb_available():
        store_path = store_path.with_suffix(".sqlite")
    monkeypatch.setenv("XVA_DICTIONARY_PATH", str(ROOT / "landing" / "desk_dictionary.yaml"))
    monkeypatch.setenv("XVA_STORE_PATH", str(store_path))
    store.dictionary.cache_clear()
    yield folder, store_path
    store.dictionary.cache_clear()


def load(folder, store_path, *args):
    return ldc.main(["--config", str(CONFIG), "--store", str(store_path), "--folder", str(folder), *args])


def edit(path: Path, fn) -> None:
    rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
    fn(rows)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def by_cpty_totals(folder: Path, d: date) -> dict[str, float]:
    with open(folder / f"XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d:%Y%m%d}.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {m: sum(float(r[m]) for r in rows) for m in ("CVA", "CVACO", "DVACO", "FVA", "XVA")}


def test_loads_all_dates_and_tools_answer(desk, capsys):
    folder, store_path = desk
    assert load(folder, store_path, "--all") == 0
    expected = by_cpty_totals(folder, D2)

    s = tools.call("get_xva_summary", {"as_of": D2, "level": "entity"})
    assert list(s.totals.components) == ["CVA", "CVACO", "DVA", "FVA"]
    assert s.totals.total == pytest.approx(expected["XVA"], abs=0.05)  # CVACO is not added in
    assert s.totals.components["CVACO"] == pytest.approx(expected["CVACO"], abs=0.05)
    assert {r.key for r in s.rows} == {"BMO", "BCMC", "BCML"}

    status = tools.call("get_data_status", {"as_of": D2})
    assert status.all_passed and status.latest_clean_date == D2

    hist = tools.call("get_xva_history", {"start": date(2026, 10, 1), "end": D2})
    assert hist.basis.startswith("Desk total history file") and hist.last.total == pytest.approx(expected["XVA"], abs=0.05)

    xva = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": "IR", "group_by": "risk_factor"})
    cva = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": "IR", "component": "CVA"})
    fva = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": "IR", "component": "FVA"})
    assert xva.measure == "pv01" and xva.component == "XVA"
    assert xva.total == pytest.approx(cva.total + fva.total, abs=0.01)
    cs = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": "CR", "measure": "cs01_parallel"})
    assert {r.key for r in cs.rows} <= {"BMO_CASH", "BMO_CDS", "CPTY_CDS"}

    found = tools.call("search_counterparties", {"query": "1SE"}).matches[0]
    assert found.counterparty_id == "1SEBK" and found.segment == "SSA"
    assert found.rating and found.credit_curve.startswith("FS:") and found.cpty_type in ("LOW_IG", "S_P_D")

    # every asset class in the desk's header list lands, with the dictionary's first measure as default
    for asset_class, measure in [("FX", "delta"), ("CO", "delta"), ("EQ", "delta"), ("CARRY", "carry_1d"),
                                 ("CR", "cs01"), ("IR", "pv01")]:
        rep = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": asset_class})
        assert rep.measure == measure and rep.rows, asset_class
    for asset_class, measure in [("IR", "vega"), ("FX", "gamma_up_x"), ("CO", "spot_delta"), ("CR", "cr01_1pct"),
                                 ("CO", "delta_parallel")]:
        assert tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": asset_class, "measure": measure}).rows
    # parallel and tenor measures are stored apart, so tenors never get the parallel added in
    tenor = tools.call("get_xva_sensitivities", {"as_of": D2, "asset_class": "IR", "measure": "pv01", "group_by": "bucket",
                                                 "top_n": 200})
    assert all(" parallel" not in r.label for r in tenor.rows)

    with pytest.raises(tools.DataMissing, match="P&L explain is loaded"):
        tools.call("get_pnl_attribution", {"start": D1, "end": D2})


def test_executive_sees_entities_not_counterparties(desk, monkeypatch):
    folder, store_path = desk
    load(folder, store_path, "--all")
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    assert tools.call("get_xva_summary", {"as_of": D2, "level": "entity"}).rows
    assert tools.call("get_xva_history", {"start": D1, "end": D2}).points
    with pytest.raises(tools.ToolDenied):
        tools.call("get_xva_movers", {"start": D1, "end": D2, "level": "counterparty"})


def test_counterparty_curve_and_attributes_are_stored(desk):
    folder, store_path = desk
    load(folder, store_path, "--all")
    from xva_mcp import db

    with db.connect(store_path, read_only=True) as con:
        rows = con.execute("SELECT tenor_months, spread, credit_curve FROM xva_cpty_curve WHERE as_of = ? AND "
                           "counterparty_id = '100IN' ORDER BY tenor_months", [D2]).fetchall()
        cols = db.table_columns(con, "dim_counterparty")
    assert [r[0] for r in rows] == [6, 12, 24, 36, 48, 60, 84, 120, 180, 240, 360, 600]
    assert rows[0][2].startswith("FS:") and "attributes_as_of" in cols


def test_reload_is_harmless_and_list_shows_status(desk, capsys):
    folder, store_path = desk
    load(folder, store_path, "--all")
    before = tools.call("get_xva_summary", {"as_of": D2}).totals.total
    assert load(folder, store_path, "--all") == 0 and "Nothing to load" in capsys.readouterr().out
    assert load(folder, store_path, "--as-of", "2026-11-24") == 0
    assert tools.call("get_xva_summary", {"as_of": D2}).totals.total == pytest.approx(before)
    load(folder, store_path, "--list")
    assert "2026-11-24  loaded XVA_SYSTEM-20261124-" in capsys.readouterr().out


def test_restatement_replaces_the_day(desk):
    folder, store_path = desk
    load(folder, store_path, "--all")
    old = tools.call("get_xva_summary", {"as_of": D2}).totals.total

    def bump(rows):  # add 1,000 to CVA and XVA of the first counterparty
        rows[1][3] = str(float(rows[1][3]) - 1000)
        rows[1][7] = str(float(rows[1][7]) - 1000)

    edit(folder / "XVA_CVA_CVACO_DVACO_FVA_by_Cpty_20261124.csv", bump)
    assert load(folder, store_path, "--as-of", "2026-11-24") == 0
    s = tools.call("get_xva_summary", {"as_of": D2})
    assert s.totals.total == pytest.approx(old - 1000, abs=0.05)
    status = tools.call("get_data_status", {"as_of": D2})
    assert "differs from desk history" in (status.sources[0].notes or "")  # history file not restated


@pytest.mark.parametrize(
    "file, change, message",
    [
        ("XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d}.csv", lambda r: r[1].__setitem__(7, "1"), "XVA differs from"),
        ("XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d}.csv", lambda r: r[1].__setitem__(0, "11/25/2026 0:00"),
         "do not match the file date"),
        ("XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d}.csv", lambda r: r.append(list(r[1])), "duplicate"),
        ("XVA_CVA_CVACO_DVACO_FVA_by_Cpty_{d}.csv", lambda r: r[1].__setitem__(3, "#N/A"), "non-numeric"),
        ("CVA_Sens_{d}.csv", lambda r: [x.append("1") for x in r[1:]] and None or r[0].append("USDCAD_FX01"),
         "match no rule"),
    ],
)
def test_bad_files_reject_the_day(desk, file, change, message, capsys):
    folder, store_path = desk
    edit(folder / file.format(d="20261124"), change)
    assert load(folder, store_path, "--as-of", "2026-11-24") == 1
    assert message in capsys.readouterr().out
    status = tools.call("get_data_status", {"as_of": D2}) if store_path.exists() else None
    assert status is not None and not status.all_passed and message in status.sources[0].notes


def test_values_only_when_sensitivities_are_broken(desk):
    folder, store_path = desk
    edit(folder / "CVA_Sens_20261124.csv", lambda r: [x.append("1") for x in r[1:]] and None or r[0].append("ODD"))
    assert load(folder, store_path, "--as-of", "2026-11-24") == 1
    assert load(folder, store_path, "--as-of", "2026-11-24", "--values-only") == 0
    assert tools.call("get_xva_summary", {"as_of": D2}).rows


def test_control_file_mismatch_is_a_note_unless_strict(desk, tmp_path):
    folder, store_path = desk
    edit(folder / "XVA_Sens_Cons_20261124.csv", lambda r: r[1].__setitem__(1, "99999"))
    assert load(folder, store_path, "--as-of", "2026-11-24") == 0
    assert "column totals differ" in tools.call("get_data_status", {"as_of": D2}).sources[0].notes

    strict = tmp_path / "strict.yaml"
    strict.write_text(CONFIG.read_text(encoding="utf-8").replace("strict_tieouts: false", "strict_tieouts: true")
                      .replace("dictionary: desk_dictionary.yaml", f"dictionary: '{ROOT / 'landing' / 'desk_dictionary.yaml'}'")
                      .replace("stored_procs: stored_procs.yaml", f"stored_procs: '{ROOT / 'landing' / 'stored_procs.yaml'}'"),
                      encoding="utf-8")
    edit(folder / "XVA_Sens_Cons_20261124.csv", lambda r: r[1].__setitem__(1, "88888"))
    code = ldc.main(["--config", str(strict), "--store", str(store_path), "--folder", str(folder), "--as-of", "2026-11-24"])
    assert code == 1


def test_setup_problems(desk, tmp_path, capsys):
    folder, store_path = desk
    assert load(tmp_path / "nowhere", store_path, "--all") == 2
    assert "not reachable" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["11/25/2026 12:00:00AM", "11/25/2026 12:00:00 AM", "11/25/2026 0:00",
                                  "11/25/2026", "2026-11-25 00:00:00", "11/25/2026 00:00:00.000"])
def test_cob_date_styles(text):
    formats = ldc.load_config(CONFIG)["date_formats"]
    assert ldc.parse_date(text, formats) == date(2026, 11, 25)


def test_rename_existing_store(desk):
    from xva_landing.schema import rename_measure

    folder, store_path = desk
    load(folder, store_path, "--all")
    counts = rename_measure(store_path, "DVA", "DVACO")  # as stores loaded before report_as existed
    assert counts["xva_value"] > 0
    rename_measure(store_path, "DVACO", "DVA")
    assert "DVA" in tools.call("get_xva_summary", {"as_of": D2}).totals.components

