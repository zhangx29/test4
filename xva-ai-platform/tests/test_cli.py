"""The command line gives the same answers and refusals as the MCP tools, locally and through the REST API."""

import json

import pytest

from xva_mcp import cli

D1, D2 = "2026-11-19", "2026-11-20"


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_tools_lists_every_command(capsys):
    code, out, _ = run(capsys, "tools")
    assert code == 0
    for short in cli.COMMANDS:
        assert f"  {short}" in out
    assert "--asset-class X" in out and "[--as-of X]" in out  # required vs optional


def test_text_answer_ends_with_provenance(capsys):
    code, out, _ = run(capsys, "summary", "--as-of", D1, "--level", "book")
    assert code == 0
    assert "XVA by book on 2026-11-19" in out and "Total (all groups)" in out
    assert out.strip().splitlines()[-1].startswith("Source: XVA system official EOD run")


def test_json_matches_the_tool(capsys):
    from xva_mcp import tools

    code, out, _ = run(capsys, "--format", "json", "pnl", "--start", D1, "--end", D2)
    assert code == 0
    data = json.loads(out)
    expected = tools.get_pnl_attribution(**tools.validate_args("get_pnl_attribution", {"start": D1, "end": D2}))
    assert data["xva_change"] == pytest.approx(expected.xva_change)
    assert [d["driver"] for d in data["drivers"]] == [d.driver for d in expected.drivers]


def test_full_tool_name_works_as_command(capsys):
    code, out, _ = run(capsys, "get_data_status", "--as-of", D2)
    assert code == 0 and "WARNING - not all sources passed" in out


def test_executive_is_refused_counterparty_detail(capsys, monkeypatch):
    code, out, err = run(capsys, "--role", "xva-exec", "movers", "--start", D1, "--end", D2)
    assert code == 3 and out == ""
    assert "Counterparty-level detail is not available" in err


def test_bad_input_and_missing_data(capsys):
    code, _, err = run(capsys, "summary", "--as-of", "2026-13-01")
    assert code == 2 and "Invalid input" in err
    code, _, err = run(capsys, "summary", "--as-of", "2026-12-25")
    assert code == 4 and "No XVA data for 2026-12-25" in err


def test_local_mode_needs_dev(capsys, monkeypatch):
    monkeypatch.delenv("XVA_ENV")
    code, _, err = run(capsys, "status")
    assert code == 2 and "development only" in err


def test_audit_says_via_cli(capsys, tmp_path):
    import os

    run(capsys, "status", "--as-of", D2)
    lines = [json.loads(x) for x in open(os.environ["XVA_AUDIT_LOG"], encoding="utf-8")]
    assert lines[-1]["via"] == "cli" and lines[-1]["tool"] == "get_data_status"


def test_api_mode_goes_through_rest(capsys, monkeypatch):
    """--api calls the REST API; here the HTTP call is routed to an in-process app."""
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from xva_mcp import rest

    client = TestClient(rest.create_app())

    def fake_get(url, token, timeout=30.0):
        path = url.split("://", 1)[1].split("/", 1)[1]
        r = client.get("/" + path)
        return r.status_code, r.json()

    monkeypatch.setattr(cli, "http_get", fake_get)
    code, out, _ = run(capsys, "--api", "http://xva-api.test", "summary", "--as-of", D1, "--level", "book")
    assert code == 0 and "XVA-CORP" in out

    monkeypatch.setenv("XVA_DEV_ROLE", "xva-exec")
    code, _, err = run(capsys, "--api", "http://xva-api.test", "movers", "--start", D1, "--end", D2)
    assert code == 3 and "not available for your role" in err

    code, _, err = run(capsys, "--api", "http://xva-api.test", "--role", "xva-desk", "status")
    assert code == 2 and "development only" in err
