"""Shared helpers for the marketplace scripts (stdlib + PyYAML only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
BEGIN, END = "<!-- BEGIN DESK RULES -->", "<!-- END DESK RULES -->"
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def read_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    return data, text[match.end():]


def shared_rules(root: Path = ROOT) -> str:
    return (root / "shared" / "desk-rules.md").read_text(encoding="utf-8").strip()


def agent_files(root: Path = ROOT) -> list[Path]:
    return sorted(root.glob("plugins/*/com.github.copilot/agents/*.agent.md"))


def m365_files(root: Path = ROOT) -> list[Path]:
    """Microsoft 365 Copilot agent instructions (pasted into the declarative agent or Copilot Studio)."""
    return sorted(root.glob("m365/*.instructions.md"))


def rule_targets(root: Path = ROOT) -> list[Path]:
    """Every file that carries a copy of the shared desk rules."""
    return agent_files(root) + m365_files(root)


def rules_block(text: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip()


def with_rules(text: str, rules: str) -> str:
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{rules}\n{END}{tail}"
