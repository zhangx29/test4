"""Landing real extracts: mapping, checks, restatements, and what the tools then read."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from xva_landing import land_extract as le
from xva_landing.schema import create_store
from xva_mcp import db, store

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "landing" / "templates"
D = date(2026, 11, 20)


@pytest.fixture
def cfg():
    return le.load_config(ROOT / "landing" / "landing_config.example.yaml")


@pytest.fixture
def real_store(tmp_path, monkeypatch, cfg):
    path = create_store(tmp_path / "store" / "xva_store.sqlite")
    assert le.land_counterparties(cfg, path, TEMPLATES / "counterparties.csv") == []
    monkeypatch.setenv("XVA_STORE_PATH", str(path))
    return path


def write(path: Path, text: str) -> Path:
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


def land_templates(cfg, store_path, **kw):
    files = {
        "xva_values": TEMPLATES / "xva_values.csv",
        "sensitivities": TEMPLATES / "sensitivities.csv",
        "pnl_explain": TEMPLATES / "pnl_explain.csv",
    }
    return le.land_xva(cfg, store_path, D, files, **kw)


def test_templates_land_and_tools_read_them(cfg, real_store, tmp_path):
    run_id, failures, _ = land_templates(cfg, real_store, controls={"xva_values": -1950000}, archive=tmp_path / "arc")
    assert failures == []
    s = store.xva_summary(D, "book", 10)
    assert {r.key: round(r.total) for r in s.rows} == {"XVA-CORP": -1575000, "XVA-FIN": -375000}
    assert s.provenance.run_ids == [run_id]
    sens = store.sensitivities(D, "IR", "delta", "bucket", None, 10)
    assert round(sens.total, 2) == -825.85
    assert len(list((tmp_path / "arc" / "2026-11-20" / "xva_system").iterdir())) == 3
    status = store.data_status(D)
    assert [x.source for x in status.sources if x.checks_passed] == ["xva_system"]


def test_rerun_is_idempotent(cfg, real_store):
    land_templates(cfg, real_store)
    land_templates(cfg, real_store)
    assert round(store.xva_summary(D, "desk", 5).totals.total) == -1950000


def test_restatement_replaces_official_run_without_double_counting(cfg, real_store, tmp_path):
    land_templates(cfg, real_store)
    restated = write(tmp_path / "v2.csv", (TEMPLATES / "xva_values.csv").read_text().replace("-1250000.00", "-1300000.00"))
    run2, failures, _ = le.land_xva(cfg, real_store, D, {"xva_values": restated})
    assert failures == []
    s = store.xva_summary(D, "desk", 5)
    assert round(s.totals.total) == -2000000 and s.provenance.run_ids == [run2]
    with db.connect(real_store) as con:  # both runs are kept for audit
        assert con.execute("SELECT COUNT(DISTINCT run_id) FROM xva_value").fetchone()[0] == 2


def test_failed_restatement_keeps_the_official_run(cfg, real_store, tmp_path):
    run1, _, _ = land_templates(cfg, real_store)
    bad = write(tmp_path / "bad.csv", "as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CP900,N,CVA,abc")
    _, failures, _ = le.land_xva(cfg, real_store, D, {"xva_values": bad})
    assert failures
    status = store.data_status(D)
    row = [x for x in status.sources if x.source == "xva_system"][0]
    assert row.run_id == run1 and row.checks_passed and "rejected" in row.notes
    assert round(store.xva_summary(D, "desk", 5).totals.total) == -1950000


@pytest.mark.parametrize(
    "csv_text, expected",
    [
        ("as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CP999,N,CVA,1", "not in the counterparty table"),
        ("as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CP900,N,XVA,1", "unknown measures"),
        ("as_of,counterparty_id,netting_set_id,measure,value\n2026-11-19,CP900,N,CVA,1", "do not match the business date"),
        ("as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CP900,N,CVA,1\n2026-11-20,CP900,N,CVA,2",
         "duplicate rows"),
        ("as_of,counterparty_id,measure\n2026-11-20,CP900,CVA", "missing columns value"),
        ("as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CP900,N,CVA,#N/A", "Excel error"),
    ],
)
def test_bad_files_are_rejected_and_reported(cfg, real_store, tmp_path, csv_text, expected):
    _, failures, _ = le.land_xva(cfg, real_store, D, {"xva_values": write(tmp_path / "v.csv", csv_text)})
    assert any(expected in f for f in failures), failures
    status = store.data_status(D)
    assert not status.all_passed and expected.split()[0] in (status.sources[0].notes or "")
    with pytest.raises(store.DataNotAvailable):
        store.xva_summary(D, "desk", 5)


def test_control_total_must_tie(cfg, real_store):
    _, failures, _ = land_templates(cfg, real_store, controls={"xva_values": -1000000})
    assert any("do not tie" in f for f in failures)


def test_wide_excel_with_maps_and_scale(cfg, real_store, tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "XVA"
    ws.append(["Report run 20-Nov-2026"])
    ws.append(["Business Date", "Cpty", "CVA_K", "DVA_K", "FVA_K"])
    ws.append(["2026-11-20", "CP900", -1250, 85, -410])
    ws.append(["2026-11-20", "CP901", "(320)", 40, -95])
    wb.save(tmp_path / "wide.xlsx")
    spec = {
        "file_type": "xlsx",
        "sheet": "XVA",
        "skip_rows": 1,
        "columns": {"as_of": "Business Date", "counterparty_id": "Cpty"},
        "melt": {"field": "measure", "columns": {"CVA": "CVA_K", "DVA": "DVA_K", "FVA": "FVA_K"}},
        "scale": 1000,
    }
    wide = {**cfg, "datasets": {**cfg["datasets"], "xva_values": spec}}
    _, failures, _ = le.land_xva(wide, real_store, D, {"xva_values": tmp_path / "wide.xlsx"})
    assert failures == []
    assert round(store.xva_summary(D, "desk", 5).totals.total) == -1950000


def test_value_maps_translate_labels(cfg, real_store, tmp_path):
    spec = yaml.safe_load(yaml.safe_dump(cfg["datasets"]["pnl_explain"]))
    spec["value_maps"] = {"driver": {"IR": "market_IR", "THETA": "time_decay"}}
    mapped = {**cfg, "datasets": {**cfg["datasets"], "pnl_explain": spec}}
    pnl = write(tmp_path / "p.csv", "as_of,counterparty_id,measure,driver,value\n2026-11-20,CP900,CVA,IR,-5\n"
                                    "2026-11-20,CP900,CVA,THETA,2")
    _, failures, _ = le.land_xva(mapped, real_store, D, {"xva_values": TEMPLATES / "xva_values.csv",
                                                         "pnl_explain": pnl})
    assert failures == []


def test_command_line_exit_codes(real_store, tmp_path, capsys):
    cfg_path = str(ROOT / "landing" / "landing_config.example.yaml")
    args = ["--config", cfg_path, "--store", str(real_store), "xva", "--as-of", "2026-11-20",
            "--values", str(TEMPLATES / "xva_values.csv")]
    assert le.main(args) == 0 and "loaded and official" in capsys.readouterr().out
    assert le.main(args[:-1] + [str(tmp_path / "missing.csv")]) == 2
    bad = write(tmp_path / "b.csv", "as_of,counterparty_id,netting_set_id,measure,value\n2026-11-20,CPX,N,CVA,1")
    assert le.main(args[:-1] + [str(bad)]) == 1
