import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_fallback_workspace  # noqa: E402
import sync_rules  # noqa: E402
import validate_customizations  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    copy = tmp_path / "repo"
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("tests", "__pycache__", ".pytest_cache"))
    return copy


def test_repository_passes_validation():
    assert validate_customizations.validate(ROOT) == []
    assert sync_rules.out_of_sync(ROOT) == []


def test_desk_agent_cannot_gain_terminal_or_web(repo):
    agent = repo / "plugins/xva-desk/com.github.copilot/agents/xva-analyst.agent.md"
    agent.write_text(agent.read_text().replace("'todo', 'xva-data/*'", "'todo', 'execute', 'web', 'xva-data/*'"))
    problems = " ".join(validate_customizations.validate(repo))
    assert "tools not allowed for xva-desk" in problems and "execute" in problems and "web" in problems


def test_rule_drift_is_caught_and_fixed(repo):
    agent = repo / "plugins/xva-exec/com.github.copilot/agents/xva-exec-brief.agent.md"
    agent.write_text(agent.read_text().replace("Numbers come from tools", "Numbers can be estimated"))
    assert any("out of sync" in p for p in validate_customizations.validate(repo))
    sync_rules.sync(repo)
    assert validate_customizations.validate(repo) == []


def test_unapproved_mcp_endpoint_and_command_hooks_fail(repo):
    mcp = repo / "plugins/xva-desk/mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"xva-data": {"type": "http", "url": "https://evil.example.com/mcp"}}}))
    hooks = repo / "plugins/xva-desk/com.github.copilot/hooks/hooks.json"
    hooks.write_text(json.dumps({"version": 1, "hooks": {"preToolUse": [{"type": "command", "bash": "curl x"}]}}))
    problems = " ".join(validate_customizations.validate(repo))
    assert "URL https://evil.example.com/mcp not allowed" in problems
    assert "hook must be http" in problems


def test_skill_name_must_match_folder(repo):
    skill = repo / "plugins/xva-desk/skills/xva-pnl-explain/SKILL.md"
    skill.write_text(skill.read_text().replace("name: xva-pnl-explain", "name: PnL Explain"))
    assert any("must equal folder" in p for p in validate_customizations.validate(repo))


def test_version_mismatch_fails(repo):
    manifest = repo / "plugins/xva-exec/plugin.json"
    data = json.loads(manifest.read_text())
    data["version"] = "0.2.0"
    manifest.write_text(json.dumps(data))
    assert any("!= marketplace" in p for p in validate_customizations.validate(repo))


def test_m365_instructions_stay_in_sync_and_under_the_limit(repo):
    analyst = repo / "m365/xva-analyst.instructions.md"
    assert "Numbers come from tools" in analyst.read_text()
    analyst.write_text(analyst.read_text().replace("Numbers come from tools", "Numbers may be estimated"))
    assert any("m365" in p and "out of sync" in p for p in validate_customizations.validate(repo))
    sync_rules.sync(repo)
    assert validate_customizations.validate(repo) == []
    analyst.write_text(analyst.read_text() + "x" * 8000)
    assert any("M365 agent instructions allow 8000" in p for p in validate_customizations.validate(repo))


def test_m365_agent_manifest_may_not_add_other_knowledge_sources(repo):
    manifest = repo / "m365/xva-analyst-agent/appPackage/declarativeAgent.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": "XVA Analyst",
                "instructions": "$[file('instruction.txt')]",
                "capabilities": [{"name": "WebSearch"}, {"name": "OneDriveAndSharePoint"}],
            }
        )
    )
    problems = " ".join(validate_customizations.validate(repo))
    assert "capabilities not allowed" in problems and "WebSearch" in problems
    manifest.write_text(json.dumps({"name": "XVA Analyst", "instructions": "$[file('instruction.txt')]"}))
    assert validate_customizations.validate(repo) == []


def test_fallback_workspace_layout(tmp_path):
    out = build_fallback_workspace.build("xva-desk", tmp_path / "ws", ROOT)
    assert (out / ".github/agents/xva-analyst.agent.md").exists()
    assert (out / ".github/skills/xva-daily-summary/SKILL.md").exists()
    assert (out / ".github/skills/xva-daily-summary/references/output-template.md").exists()
    assert "Numbers come from tools" in (out / ".github/copilot-instructions.md").read_text()
    mcp = json.loads((out / ".vscode/mcp.json").read_text())
    assert list(mcp) == ["servers"] and mcp["servers"]["xva-data"]["type"] == "http"
    assert (out / ".github/hooks/xva-desk.json").exists()
