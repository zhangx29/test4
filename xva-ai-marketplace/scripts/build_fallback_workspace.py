"""Assemble a repo-based workspace from a plugin, for environments without managed plugins
(GitHub Enterprise Server, or before the platform team enables enterprise-managed settings).

    python scripts/build_fallback_workspace.py xva-desk ../xva-desk-workspace

Produces .github/copilot-instructions.md, .github/agents/, .github/skills/, .github/hooks/ and
.vscode/mcp.json (VS Code format: "servers" instead of "mcpServers"). Commit the result to a
repository where desk users have Read access only.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, shared_rules  # noqa: E402

HEADER = (
    "# XVA desk instructions\n\n"
    "Generated from xva-ai-marketplace/shared/desk-rules.md. Do not edit here; changes go through "
    "a pull request on xva-ai-marketplace.\n\n"
)


def build(plugin: str, out: Path, root: Path = ROOT) -> Path:
    src = root / "plugins" / plugin
    if not src.exists():
        raise SystemExit(f"Unknown plugin {plugin}")
    gh = out / ".github"
    (gh / "agents").mkdir(parents=True, exist_ok=True)
    (out / ".vscode").mkdir(parents=True, exist_ok=True)
    (out / "outputs").mkdir(parents=True, exist_ok=True)
    (out / "outputs" / ".gitkeep").touch()

    (gh / "copilot-instructions.md").write_text(HEADER + shared_rules(root) + "\n", encoding="utf-8")
    for agent in (src / "com.github.copilot" / "agents").glob("*.agent.md"):
        shutil.copy2(agent, gh / "agents" / agent.name)
    if (src / "skills").exists():
        shutil.copytree(src / "skills", gh / "skills", dirs_exist_ok=True)
    for hooks in (src / "com.github.copilot").glob("**/hooks.json"):
        (gh / "hooks").mkdir(exist_ok=True)
        shutil.copy2(hooks, gh / "hooks" / f"{plugin}.json")

    portable = json.loads((src / "mcp.json").read_text(encoding="utf-8"))
    vscode = {"servers": portable.get("mcpServers", {})}
    (out / ".vscode" / "mcp.json").write_text(json.dumps(vscode, indent=2) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(f"Wrote {build(sys.argv[1], Path(sys.argv[2]).resolve())}")
