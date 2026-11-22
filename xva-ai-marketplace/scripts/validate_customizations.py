"""CI gate for the marketplace: structure, naming, tool policy, endpoints, hooks and rule drift.

    python scripts/validate_customizations.py [repo_root]

Exits 1 and prints every problem found. Edit the POLICY block to match your bank.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, agent_files, m365_files, read_frontmatter, rules_block, shared_rules  # noqa: E402

# ----------------------------------------------------------------------------- POLICY
ALLOWED_AGENT_TOOLS = {
    "xva-desk": {"read", "search", "edit", "todo", "xva-data/*"},
    "xva-exec": {"todo", "xva-data/*"},
    "xva-dev": {"read", "search", "edit", "execute", "todo", "agent", "xva-data-staging/*"},
}
FORBIDDEN_EVERYWHERE = {"*", "web"}
ALLOWED_MCP_URLS = {
    "xva-desk": r"^https://xva-mcp\.apps\.internal\.example/mcp$",
    "xva-exec": r"^https://xva-mcp\.apps\.internal\.example/mcp$",
    "xva-dev": r"^https://xva-mcp-staging\.apps\.internal\.example/mcp$",
}
ALLOWED_HOOK_URL = r"^https://xva-audit\.apps\.internal\.example/"
M365_INSTRUCTIONS_MAX = 8000  # declarative agent manifest limit for "instructions"
# Declarative agent capabilities (WebSearch, OneDriveAndSharePoint, GraphConnectors, TeamsMessages, ...)
# would let the agent answer from sources other than xva-data. None are allowed unless listed here.
ALLOWED_M365_CAPABILITIES: set[str] = set()
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LINK_RE = re.compile(r"\]\(([^)#\s]+)\)")
# ------------------------------------------------------------------------------------


def load_json(path: Path, problems: list[str]) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{path}: invalid JSON ({exc})")
        return None


def check_marketplace(root: Path, problems: list[str]) -> None:
    market = load_json(root / ".github" / "plugin" / "marketplace.json", problems)
    if market is None:
        return
    for entry in market.get("plugins", []):
        src = (root / entry["source"]).resolve()
        manifest = load_json(src / "plugin.json", problems) if src.exists() else None
        if manifest is None:
            problems.append(f"marketplace: plugin {entry['name']} has no plugin.json at {entry['source']}")
            continue
        if manifest.get("name") != entry["name"] or manifest.get("name") != src.name:
            problems.append(f"{src.name}: plugin.json name, folder and marketplace entry must match")
        if manifest.get("version") != entry.get("version"):
            problems.append(f"{src.name}: version {manifest.get('version')} != marketplace {entry.get('version')}")
        if not str(manifest.get("$schema", "")).startswith("https://agent-plugins.org/schemas/"):
            problems.append(f"{src.name}: plugin.json must declare the Agent Plugins $schema")


def check_skills(root: Path, problems: list[str]) -> None:
    for skill_md in sorted(root.glob("plugins/*/skills/*/SKILL.md")):
        meta, body = read_frontmatter(skill_md)
        folder = skill_md.parent.name
        name, desc = meta.get("name"), meta.get("description")
        if name != folder:
            problems.append(f"{skill_md}: name {name!r} must equal folder {folder!r}")
        if not name or not NAME_RE.match(str(name)) or len(str(name)) > 64:
            problems.append(f"{skill_md}: name must be lowercase letters, digits and hyphens, max 64")
        if not desc or len(str(desc)) > 1024:
            problems.append(f"{skill_md}: description is required and at most 1024 characters")
        for link in LINK_RE.findall(body):
            if not link.startswith(("http://", "https://")) and not (skill_md.parent / link).exists():
                problems.append(f"{skill_md}: broken link {link}")


def check_agents(root: Path, problems: list[str]) -> None:
    rules = shared_rules(root)
    for agent in agent_files(root):
        plugin = agent.relative_to(root).parts[1]  # plugins/<plugin>/com.github.copilot/agents/x.agent.md
        meta, body = read_frontmatter(agent)
        for field in ("name", "description", "tools"):
            if not meta.get(field):
                problems.append(f"{agent}: missing {field}")
        tools = set(meta.get("tools") or [])
        bad = (tools - ALLOWED_AGENT_TOOLS.get(plugin, set())) | (tools & FORBIDDEN_EVERYWHERE)
        if bad:
            problems.append(f"{agent}: tools not allowed for {plugin}: {sorted(bad)}")
        if rules_block(agent.read_text(encoding="utf-8")) != rules:
            problems.append(f"{agent}: desk rules out of sync (run scripts/sync_rules.py)")


def check_mcp_and_hooks(root: Path, problems: list[str]) -> None:
    for plugin_dir in sorted((root / "plugins").iterdir()):
        mcp = load_json(plugin_dir / "mcp.json", problems) if (plugin_dir / "mcp.json").exists() else None
        for name, server in (mcp or {}).get("mcpServers", {}).items():
            pattern = ALLOWED_MCP_URLS.get(plugin_dir.name)
            if server.get("type") != "http" or "command" in server:
                problems.append(f"{plugin_dir.name}: MCP server {name} must be remote http, not a local command")
            elif not pattern or not re.match(pattern, server.get("url", "")):
                problems.append(f"{plugin_dir.name}: MCP server {name} URL {server.get('url')} not allowed")
        for hooks_file in plugin_dir.glob("**/hooks.json"):
            hooks = load_json(hooks_file, problems) or {}
            for event, entries in hooks.get("hooks", {}).items():
                for entry in entries:
                    if entry.get("type") != "http":
                        problems.append(f"{hooks_file}: {event} hook must be http (no local commands)")
                    elif not re.match(ALLOWED_HOOK_URL, entry.get("url", "")):
                        problems.append(f"{hooks_file}: {event} hook URL not allowed")


def check_m365(root: Path, problems: list[str]) -> None:
    rules = shared_rules(root)
    for path in m365_files(root):
        text = path.read_text(encoding="utf-8")
        if rules_block(text) != rules:
            problems.append(f"{path}: desk rules out of sync (run scripts/sync_rules.py)")
        if len(text) > M365_INSTRUCTIONS_MAX:
            problems.append(f"{path}: {len(text)} characters; M365 agent instructions allow {M365_INSTRUCTIONS_MAX}")
    # Agents Toolkit projects committed under m365/<agent>/ (appPackage/declarativeAgent.json)
    for manifest_path in sorted(root.glob("m365/**/declarativeAgent.json")):
        manifest = load_json(manifest_path, problems) or {}
        names = {str(c.get("name")) for c in manifest.get("capabilities", []) if isinstance(c, dict)}
        extra = names - ALLOWED_M365_CAPABILITIES
        if extra:
            problems.append(f"{manifest_path}: capabilities not allowed: {sorted(extra)} (answer from xva-data only)")
        instructions = manifest.get("instructions", "")
        if isinstance(instructions, str) and not instructions.startswith("$["):  # $[file(...)] references
            if len(instructions) > M365_INSTRUCTIONS_MAX:
                problems.append(f"{manifest_path}: instructions exceed {M365_INSTRUCTIONS_MAX} characters")
            if rules_block(instructions) not in (None, rules):
                problems.append(f"{manifest_path}: desk rules out of sync")


def validate(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    check_marketplace(root, problems)
    check_skills(root, problems)
    check_agents(root, problems)
    check_mcp_and_hooks(root, problems)
    check_m365(root, problems)
    return problems


def main() -> None:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    problems = validate(root)
    for p in problems:
        print(f"FAIL {p}")
    print(f"{len(problems)} problem(s)")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
