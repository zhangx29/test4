"""Copy shared/desk-rules.md into every agent between the DESK RULES markers: the VS Code agents
in plugins/ and the Microsoft 365 Copilot agent instructions in m365/.

    python scripts/sync_rules.py          # rewrite agents
    python scripts/sync_rules.py --check  # exit 1 if any agent is out of sync (CI)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, rule_targets, rules_block, shared_rules, with_rules  # noqa: E402


def out_of_sync(root: Path = ROOT) -> list[Path]:
    rules = shared_rules(root)
    return [p for p in rule_targets(root) if rules_block(p.read_text(encoding="utf-8")) != rules]


def sync(root: Path = ROOT) -> list[Path]:
    rules = shared_rules(root)
    changed = []
    for path in rule_targets(root):
        text = path.read_text(encoding="utf-8")
        if rules_block(text) is None:
            raise SystemExit(f"{path} has no DESK RULES markers")
        new = with_rules(text, rules)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        stale = out_of_sync()
        for p in stale:
            print(f"out of sync: {p.relative_to(ROOT)}")
        raise SystemExit(1 if stale else 0)
    for p in sync():
        print(f"updated: {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
