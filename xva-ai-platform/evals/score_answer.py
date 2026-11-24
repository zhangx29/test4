"""Score agent answers against golden questions.

Expected numbers are recomputed by calling the tool functions directly (dev identity, same store),
so the evaluation checks that the agent reported the tools' numbers, not a hard-coded copy.

    XVA_ENV=dev XVA_STORE_PATH=... python evals/score_answer.py evals/golden_questions.yaml evals/out/
    # answers are read from evals/out/<id>.md (or .txt)
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from xva_mcp import tools

NUMBER = re.compile(r"(?<![\w.])([-+−]?)\(?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\)?\s*([kKmM])?(?![\w])")
_PATH_PART = re.compile(r"^(\w+)(?:\[(?:(\d+)|(\w+)=([\w-]+))\])?$")


def _as_date_args(args: dict[str, Any]) -> dict[str, Any]:
    return {k: date.fromisoformat(v) if k in ("as_of", "start", "end") else v for k, v in args.items()}


def expected_value(spec: dict[str, Any]) -> float:
    role = spec.get("role")
    previous = os.environ.get("XVA_DEV_ROLE")
    if role:
        os.environ["XVA_DEV_ROLE"] = role
    try:
        result = getattr(tools, spec["tool"])(**_as_date_args(spec.get("args", {})))
    finally:
        if role:
            os.environ["XVA_DEV_ROLE"] = previous or "xva-desk"
    obj: Any = result.model_dump()
    for part in spec["path"].split("."):
        m = _PATH_PART.match(part)
        if not m:
            raise ValueError(f"bad path part {part}")
        name, index, key, value = m.groups()
        obj = obj[name]
        if index is not None:
            obj = obj[int(index)]
        elif key is not None:
            obj = next(item for item in obj if str(item[key]) == value)
    return float(obj)


def numbers_in_thousands(text: str) -> list[float]:
    """Read numbers as CAD thousands: 'k' as is, 'm' x1000, bare numbers >= 10,000 assumed CAD."""
    out = []
    for sign, whole, frac, suffix in NUMBER.findall(text):
        value = float(whole.replace(",", "") + (frac or ""))
        if suffix in ("m", "M"):
            value *= 1000
        elif not suffix and value >= 10_000:
            value /= 1000
        out.append(-value if sign in ("-", "−") else value)
    return out


def contains_number(text: str, expected_cad: float, tolerance_k: float = 0.15) -> bool:
    target = abs(expected_cad) / 1000
    tol = max(tolerance_k, 0.001 * target)
    return any(abs(abs(n) - target) <= tol for n in numbers_in_thousands(text))


def score(question: dict[str, Any], answer: str) -> list[str]:
    failures = []
    low = answer.lower()
    for spec in question.get("expect_numbers", []):
        value = expected_value(spec)
        if not contains_number(answer, value):
            failures.append(f"missing {spec['path']} = {value / 1000:,.1f}k from {spec['tool']}")
    for spec in question.get("must_not_include_numbers", []):
        value = expected_value(spec)
        if contains_number(answer, value):
            failures.append(f"leaked {spec['path']} = {value / 1000:,.1f}k")
    failures += [f"missing text {s!r}" for s in question.get("must_include", []) if s.lower() not in low]
    failures += [f"forbidden text {s!r}" for s in question.get("must_not_include", []) if s.lower() in low]
    return failures


def main() -> None:
    questions = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
    answers = Path(sys.argv[2])
    failed = 0
    for q in questions:
        path = next((answers / f"{q['id']}{ext}" for ext in (".md", ".txt") if (answers / f"{q['id']}{ext}").exists()), None)
        if path is None:
            print(f"{q['id']}: NO ANSWER")
            failed += 1
            continue
        if q.get("manual_review"):
            print(f"{q['id']}: MANUAL REVIEW - {q.get('notes', '')}")
            continue
        problems = score(q, path.read_text(encoding="utf-8"))
        failed += bool(problems)
        print(f"{q['id']}: {'PASS' if not problems else 'FAIL'}" + "".join(f"\n    - {p}" for p in problems))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
