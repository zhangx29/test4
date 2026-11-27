import json
import os

import pytest

EXPECTED_TOOLS = {
    "get_data_status",
    "get_xva_summary",
    "get_xva_movers",
    "get_counterparty_xva_timeseries",
    "get_xva_sensitivities",
    "get_xva_history",
    "get_pnl_attribution",
    "search_counterparties",
    "get_definition",
    "compare_counterparty_variants",
}


def ok(result):
    assert not result.isError, result.content[0].text if result.content else result
    return result.structuredContent


def test_every_tool_is_read_only_and_typed(list_tools):
    tools = {t.name: t for t in list_tools()}
    assert set(tools) == EXPECTED_TOOLS
    for tool in tools.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.outputSchema, f"{tool.name} has no output schema"
        assert tool.description and len(tool.description) > 40


def test_summary_totals_tie_across_levels(call):
    desk = ok(call("get_xva_summary", {"as_of": "2026-11-19", "level": "desk"}))
    cpty = ok(call("get_xva_summary", {"as_of": "2026-11-19", "level": "counterparty", "top_n": 3}))
    assert cpty["truncated"] is True and len(cpty["rows"]) == 3
    assert desk["totals"]["total"] == pytest.approx(cpty["totals"]["total"])
    assert desk["provenance"]["run_ids"] == ["XVA-20261119-01"]
    assert desk["provenance"]["unit"] == "CAD"


def test_row_cap_is_enforced(call):
    result = call("get_xva_summary", {"as_of": "2026-11-19", "top_n": 500})
    assert result.isError


def test_default_date_is_latest_clean_date(call):
    summary = ok(call("get_xva_summary", {}))
    assert summary["as_of"] == "2026-11-19"  # 2026-11-20 failed the MFL check in the sample data


def test_unknown_date_names_latest_available(call):
    result = call("get_xva_summary", {"as_of": "2026-12-25"})
    assert result.isError
    assert "Latest available date: 2026-11-20" in result.content[0].text


def test_movers_equal_difference_of_summaries(call):
    start = ok(call("get_xva_summary", {"as_of": "2026-11-18", "level": "counterparty", "top_n": 200}))
    end = ok(call("get_xva_summary", {"as_of": "2026-11-19", "level": "counterparty", "top_n": 200}))
    movers = ok(call("get_xva_movers", {"start": "2026-11-18", "end": "2026-11-19", "top_n": 200}))
    totals = lambda s: {r["key"]: r["total"] for r in s["rows"]}  # noqa: E731
    for row in movers["rows"]:
        assert row["change"] == pytest.approx(totals(end)[row["key"]] - totals(start)[row["key"]])
    changes = [abs(r["change"]) for r in movers["rows"]]
    assert changes == sorted(changes, reverse=True)


def test_pnl_explain_ties_to_values(call):
    attribution = ok(call("get_pnl_attribution", {"start": "2026-10-30", "end": "2026-11-19"}))
    start = ok(call("get_xva_summary", {"as_of": "2026-10-30"}))
    end = ok(call("get_xva_summary", {"as_of": "2026-11-19"}))
    assert attribution["xva_change"] == pytest.approx(end["totals"]["total"] - start["totals"]["total"])
    assert attribution["reconciliation_gap"] == pytest.approx(0.0, abs=1e-4)
    drivers = [d["driver"] for d in attribution["drivers"]]
    assert drivers[0] == "market_IR" and drivers[-1] == "unexplained"


def test_counterparty_scope_pnl(call):
    attribution = ok(
        call("get_pnl_attribution", {"start": "2026-11-18", "end": "2026-11-19", "level": "counterparty", "key": "CP001"})
    )
    movers = ok(call("get_xva_movers", {"start": "2026-11-18", "end": "2026-11-19", "top_n": 200}))
    cp001 = next(r for r in movers["rows"] if r["key"] == "CP001")
    assert attribution["xva_change"] == pytest.approx(cp001["change"])


def test_data_status_reports_failed_check_and_calendar(call):
    status = ok(call("get_data_status", {"as_of": "2026-11-20"}))
    assert status["all_passed"] is False
    failed = [s for s in status["sources"] if not s["checks_passed"]]
    assert [s["source"] for s in failed] == ["mfl_spreadsheet"]
    assert "control cell" in failed[0]["notes"]
    assert status["latest_clean_date"] == "2026-11-19"
    assert status["previous_business_date"] == "2026-11-19"
    assert status["month_start_reference"] == "2026-10-30"


def test_timeseries(call):
    ts = ok(call("get_counterparty_xva_timeseries", {"counterparty_id": "CP003", "start": "2026-11-02", "end": "2026-11-13"}))
    assert ts["name"] == "Atlas Shipping Ltd"
    assert len(ts["points"]) == 10


def test_sensitivity_units_come_from_dictionary(call):
    cs01 = ok(call("get_xva_sensitivities", {"asset_class": "CR", "measure": "cs01", "as_of": "2026-11-19"}))
    assert cs01["provenance"]["unit"] == "CAD per bp"
    assert cs01["total"] < 0
    bad = call("get_xva_sensitivities", {"asset_class": "FX", "measure": "vega"})
    assert bad.isError and "No vega sensitivity" in bad.content[0].text


def test_search_and_definitions(call):
    found = ok(call("search_counterparties", {"query": "pension"}))
    assert [m["counterparty_id"] for m in found["matches"]] == ["CP002"]
    cva = ok(call("get_definition", {"term": "cva"}))
    assert cva["found"] and cva["entry"]["value"]["unit"] == "CAD"
    sign = ok(call("get_definition", {"term": "sign convention"}))
    assert "negative = cost" in sign["entry"]["value"]
    missing = ok(call("get_definition", {"term": "KVA"}))
    assert missing["found"] is False and "CVA" in missing["available_terms"]


def test_every_call_is_audited(call):
    ok(call("get_xva_summary", {"as_of": "2026-11-19"}))
    lines = [json.loads(line) for line in open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8")]
    last = lines[-1]
    assert last["tool"] == "get_xva_summary"
    assert last["user"] == "test-user" and last["role"] == "xva-desk" and last["client"] == "dev"
    assert last["outcome"] == "ok" and last["rows"] == 1
