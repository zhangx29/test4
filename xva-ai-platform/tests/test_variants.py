"""Counterparty variants (XVATEST-...): loaded next to the official data, compared on request, never in a total."""

import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

import fake_sp
from sample_data.make_desk_files import build
from xva_landing import load_desk_csv as ldc
from xva_landing import variants as var
from xva_mcp import db, render, store, tools

ROOT = Path(__file__).resolve().parents[1]
D1, D2 = date(2026, 11, 23), date(2026, 11, 24)
MAPPING = {"variants": [
    {"code": "XVATEST-100IN", "base": "100IN", "method": "Collateral modelled with a zero threshold", "owner": "Desk"},
    {"code": "XVATEST-100IN2", "base": "100IN", "method": "Stressed funding spread", "owner": "Desk"},
]}
TEST_PROCS = {
    "xva_values": {"db": "CVA@CMDB", "sp": "APP_TEST.SP_GET_XVA_BY_CPTY",
                   "params": {"p_PERIODSTART": "{start}", "p_PERIODEND": "{end}", "P_CPTY_CODE": "{cpty_codes}",
                              "P_ExcludedCpty": ["XVATEST", "CMLEASE"]}},
    "xva_all01": {"db": "CVA@CMDB", "sp": "APP_TEST.SP_GET_XVA_ALL01",
                  "params": {"p_PERIODSTART": "{start}", "P_CPTY_CODE": "{cpty_codes}",
                             "P_ExcludedCpty": ["XVATEST", "CMLEASE"]}},
    **{f"sens_{c.lower()}": {"db": "CVA@CMDB", "sp": "APP_TEST.SP_GET_XVA_SENS",
                             "params": {"p_PERIODSTART": "{start}", "p_PERIODEND": "{end}", "P_XVAType": c,
                                        "P_CPTY_CODE": "{cpty_codes}", "P_ExcludedCpty": ["XVATEST", "CMLEASE"]}}
       for c in ("CVA", "FVA", "XVA")},
}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Official data from the CSV folder; variants through the (fake) procedures for values, sensitivities, P&L."""
    folder = build(tmp_path / "csv", [D1, D2])
    fake_sp.VALUES_FOLDER, fake_sp.LAYOUT = folder, "wide"
    reg = yaml.safe_load((ROOT / "landing" / "stored_procs.yaml").read_text(encoding="utf-8"))
    reg["backend"], reg["callable"] = "python", "fake_sp:run_sp"
    reg["procedures"].update(TEST_PROCS)
    (tmp_path / "procs.yaml").write_text(yaml.safe_dump(reg), encoding="utf-8")
    (tmp_path / "variants.yaml").write_text(yaml.safe_dump(MAPPING), encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "landing" / "desk_csv.yaml").read_text(encoding="utf-8"))
    cfg.update(folder=str(folder), stored_procs=str(tmp_path / "procs.yaml"),
               dictionary=str(ROOT / "landing" / "desk_dictionary.yaml"), sp_output_folder=str(tmp_path / "spo"))
    cfg["values"]["procedure"] = "xva_values"
    for f in cfg["sensitivities"]["files"]:
        f["procedure"] = f"sens_{f['component'].lower()}"
    cfg["credit_spreads"]["procedure"] = "xva_all01"
    cfg["variants"].update(mapping=str(tmp_path / "variants.yaml"),
                           datasets=["values", "sensitivities", "credit_spreads", "pnl_explain"])
    conf = tmp_path / "desk_csv.yaml"
    conf.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    suffix = ".duckdb" if db.duckdb_available() else ".sqlite"
    monkeypatch.setenv("XVA_DICTIONARY_PATH", str(ROOT / "landing" / "desk_dictionary.yaml"))
    store.dictionary.cache_clear()
    yield conf, folder, tmp_path, suffix
    store.dictionary.cache_clear()
    fake_sp.FAIL.clear()


def load(conf, store_path, *args):
    return ldc.main(["--config", str(conf), "--store", str(store_path), *args])


def use(store_path, monkeypatch):
    monkeypatch.setenv("XVA_STORE_PATH", str(store_path))


def answers():
    """What every official tool says about the desk; must not change when variants are loaded."""
    out = {}
    for level in ("desk", "entity", "book", "counterparty"):
        r = tools.call("get_xva_summary", {"as_of": D2, "level": level, "top_n": 200})
        out[f"summary_{level}"] = (r.totals.model_dump(), [x.model_dump() for x in r.rows])
        m = tools.call("get_xva_movers", {"start": D1, "end": D2, "level": level, "top_n": 200})
        out[f"movers_{level}"] = m.model_dump(exclude={"provenance"})
    out["pnl"] = tools.call("get_pnl_attribution", {"start": D1, "end": D2}).model_dump(exclude={"provenance"})
    for ac in ("IR", "FX", "CR"):
        out[f"sens_{ac}"] = tools.call("get_xva_sensitivities", {"asset_class": ac, "as_of": D2, "group_by": "counterparty",
                                                                "top_n": 200}).model_dump(exclude={"provenance"})
    out["history"] = tools.call("get_xva_history", {"start": D1, "end": D2}).model_dump(exclude={"provenance"})
    out["search"] = tools.call("search_counterparties", {"query": "XVATEST"}).model_dump()
    return out


def _rounded(x):
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v) for v in x]
    return x


def test_variants_never_change_an_official_answer(setup, monkeypatch, capsys):
    conf, folder, tmp, suffix = setup
    with_variants, without = tmp / f"with{suffix}", tmp / f"without{suffix}"
    assert load(conf, with_variants, "--from", "2026-11-23", "--to", "2026-11-24") == 0
    out = capsys.readouterr().out
    assert "variants=sp" in out and "2026-11-24 variants: 2 loaded" in out
    assert load(conf, without, "--from", "2026-11-23", "--to", "2026-11-24", "--source", "variants=off") == 0

    with db.connect(with_variants, read_only=True) as con:
        assert con.execute("SELECT COUNT(*) FROM xva_variant_value").fetchone()[0] > 0
        assert con.execute("SELECT COUNT(*) FROM xva_variant_sensitivity").fetchone()[0] > 0
        assert con.execute("SELECT COUNT(*) FROM xva_variant_pnl_explain").fetchone()[0] > 0
        for table in ("xva_value", "xva_sensitivity", "xva_pnl_explain", "xva_pnl_actual", "dim_counterparty"):
            assert con.execute(f"SELECT COUNT(*) FROM {table} WHERE counterparty_id LIKE '%XVATEST%'").fetchone()[0] == 0
        # only the variants: no copy of counterparties that have none
        cps = {r[0] for r in con.execute("SELECT DISTINCT counterparty_id FROM xva_variant_value").fetchall()}
        assert cps == {"XVATEST-100IN", "XVATEST-100IN2"}  # XVATEST-102BK is not in variants.yaml

    use(with_variants, monkeypatch)
    a = answers()
    status = tools.call("get_data_status", {"as_of": D2})
    assert status.all_passed and "xva_variants" in {s.source for s in status.sources}, status.model_dump()
    use(without, monkeypatch)
    b = answers()
    for k in a:  # same answers; amounts compared to 1e-9 (the database may add in a different order)
        assert _rounded(b[k]) == _rounded(a[k]), k


def test_compare_counterparty_variants(setup, monkeypatch):
    conf, folder, tmp, suffix = setup
    store_path = tmp / f"s{suffix}"
    assert load(conf, store_path, "--from", "2026-11-23", "--to", "2026-11-24") == 0
    use(store_path, monkeypatch)
    r = tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2})
    assert r.counterparties == ["100IN", "XVATEST-100IN", "XVATEST-100IN2"] and not r.missing
    official, v1, v2 = r.values
    assert official.role == "official" and v1.method == "Collateral modelled with a zero threshold"
    assert v1.total == pytest.approx(official.total * 1.25) and v2.total == pytest.approx(official.total * 0.8)
    assert v1.total_vs_official == pytest.approx(official.total * 0.25)
    assert set(official.components) >= {"CVA", "DVA", "FVA"}  # DVACO reported as DVA, as everywhere
    ir = next(s for s in r.sensitivities if s.asset_class == "IR" and s.values["100IN"])
    assert ir.values["XVATEST-100IN"] == pytest.approx(ir.values["100IN"] * 1.25)
    cr = next(s for s in r.sensitivities if s.asset_class == "CR" and s.measure == "cs01_parallel")
    assert cr.values["XVATEST-100IN2"] == pytest.approx(cr.values["100IN"] * 0.8)
    assert r.risk_factor_differences and r.risk_factor_differences[0].largest_difference > 0
    assert {g.group for g in r.pnl_groups} >= {"Interest rates", "Unexplained"}
    assert set(r.actual_pl) == {"100IN", "XVATEST-100IN", "XVATEST-100IN2"}
    assert r.pnl_start == D1 and r.variant_run_ids

    # a variant ID finds the same comparison; CVA picks that component
    same = tools.call("compare_counterparty_variants", {"counterparty_id": "xvatest-100in2", "as_of": D2,
                                                        "component": "CVA"})
    assert same.base_id == "100IN" and same.component == "CVA"
    text = render.render(r)
    assert "never added to any total" in text and "100IN (official)" in text and "Stressed funding spread" in text
    with pytest.raises(tools.DataMissing, match="Counterparties with variants: 100IN"):
        tools.call("compare_counterparty_variants", {"counterparty_id": "102BK", "as_of": D2})


def test_executives_cannot_see_variants(setup, monkeypatch):
    conf, folder, tmp, suffix = setup
    store_path = tmp / f"s{suffix}"
    assert load(conf, store_path, "--as-of", "2026-11-24") == 0
    use(store_path, monkeypatch)
    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    with pytest.raises(tools.ToolDenied):
        tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2})


def test_official_files_never_bring_in_a_variant(setup, monkeypatch):
    conf, folder, tmp, suffix = setup
    path = folder / "XVA_CVA_CVACO_DVACO_FVA_by_Cpty_20261124.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.append(lines[1].replace(lines[1].split(",")[2], "XVATEST-100IN", 1))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store_path = tmp / f"s{suffix}"
    cfg = ldc.load_config(conf)
    res = ldc.read_day(cfg, folder, D2, fetcher=ldc.Fetcher(cfg, folder, {"pnl_explain": "off"}))
    assert res.loaded and any("1 XVATEST counterparties left out of official data" in n for n in res.notes)
    assert not [r for r in res.values if "XVATEST" in r[2]]
    ldc.write_day(cfg, store_path, res)
    use(store_path, monkeypatch)
    assert not tools.call("search_counterparties", {"query": "XVATEST"}).matches


def test_variant_problems_are_notes(setup, monkeypatch, capsys):
    conf, folder, tmp, suffix = setup
    store_path = tmp / f"s{suffix}"
    fake_sp.FAIL.add("FVA")  # the P&L procedure fails for FVA, for official and variant calls alike
    assert load(conf, store_path, "--as-of", "2026-11-24") == 0
    out = capsys.readouterr().out
    assert "variants: 2 loaded" in out and "variant P&L FVA not loaded" in out
    use(store_path, monkeypatch)
    status = tools.call("get_data_status", {"as_of": D2})
    assert status.all_passed  # a variant note never marks the official day as failed
    r = tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2, "component": "FVA"})
    assert any("no FVA P&L" in m for m in r.missing)


def test_a_dataset_without_variant_rows_is_missing_not_zero(setup, monkeypatch):
    conf, folder, tmp, suffix = setup
    cfg = yaml.safe_load(conf.read_text(encoding="utf-8"))
    cfg["variants"]["datasets"] = ["values", "sensitivities", "pnl_explain"]  # no credit spreads for variants
    conf.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    store_path = tmp / f"s{suffix}"
    assert load(conf, store_path, "--as-of", "2026-11-24") == 0
    use(store_path, monkeypatch)
    r = tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2})
    cr = next(s for s in r.sensitivities if s.asset_class == "CR" and s.measure == "cs01_parallel")
    assert set(cr.values) == {"100IN"} and cr.largest_difference is None
    assert "-" in render.render(r)


def test_variants_only_find_and_csv_mode(setup, monkeypatch, capsys):
    conf, folder, tmp, suffix = setup
    store_path = tmp / f"s{suffix}"
    assert load(conf, store_path, "--as-of", "2026-11-24", "--source", "variants=off") == 0
    assert load(conf, store_path, "--as-of", "2026-11-24", "--variants-only") == 0
    capsys.readouterr()
    use(store_path, monkeypatch)
    from_sp = tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2})

    # find: XVATEST-102BK is returned by the procedures but not listed
    assert load(conf, store_path, "--as-of", "2026-11-24", "--find-variants") == 0
    out = capsys.readouterr().out
    assert "1 variant codes on 2026-11-24 are not in variants.yaml" in out and "XVATEST-102BK" in out

    # the saved VARIANTS_ files load the same way from the CSV folder
    for f in (tmp / "spo" / "20261124").glob("VARIANTS_*"):
        if not f.name.startswith("VARIANTS_ALL_"):
            shutil.copy(f, folder / f.name)
    csv_store = tmp / f"csv{suffix}"
    assert load(conf, csv_store, "--as-of", "2026-11-24", "--source", "pnl_explain=csv",
                "--source", "variants=csv") == 0
    use(csv_store, monkeypatch)
    from_csv = tools.call("compare_counterparty_variants", {"counterparty_id": "100IN", "as_of": D2})
    assert from_csv.model_dump(exclude={"provenance", "variant_run_ids"}) == \
           from_sp.model_dump(exclude={"provenance", "variant_run_ids"})


def test_mapping_is_checked(setup):
    conf, folder, tmp, suffix = setup
    cfg = ldc.load_config(conf)
    for bad, msg in [({"variants": [{"code": "CHT2", "base": "CHT"}]}, "does not contain XVATEST"),
                     ({"variants": [{"code": "XVATEST-A", "base": "A"}, {"code": "xvatest-a", "base": "A"}]}, "twice"),
                     ({"variants": [{"code": "XVATEST-A"}]}, "needs both code and base")]:
        (tmp / "variants.yaml").write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(ldc.SetupError, match=msg):
            var.load_mapping(cfg)
    assert var.load_mapping(ldc.load_config(ROOT / "landing" / "desk_csv.yaml"))[0]["base"] == "CHT"
