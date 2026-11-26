"""The meeting demo runs end to end against the sample store through a real MCP server process."""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

ROOT = Path(__file__).resolve().parents[1]


def test_demo_runs_and_shows_refusals(store_path, tmp_path):
    audit = tmp_path / "demo_audit.jsonl"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "demo" / "xva_demo.py"), "--store", str(store_path), "--audit", str(audit)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    from xva_mcp.tools import TOOLS

    assert f"offers {len(TOOLS)} tools" in out
    assert "Desk XVA" in out
    assert "WARNING - not all sources passed" in out
    assert out.count("REFUSED by the server") == 2
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 9
