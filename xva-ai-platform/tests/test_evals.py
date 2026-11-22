from pathlib import Path

import yaml

from evals import score_answer

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "golden_questions.yaml"
QUESTIONS = {q["id"]: q for q in yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))}


def test_number_parsing_handles_k_m_and_raw_cad():
    nums = score_answer.numbers_in_thousands("CVA -1,234.5k, FVA 2.1m, DVA 456,789 CAD, 3 names")
    assert -1234.5 in nums and 2100.0 in nums and 456.789 in nums and 3.0 in nums


def test_good_answer_passes_and_bad_answer_fails():
    q = QUESTIONS["Q01"]
    total = score_answer.expected_value(q["expect_numbers"][0])
    change = score_answer.expected_value(q["expect_numbers"][1])
    good = (
        f"Total XVA on 19 Nov was {total / 1000:,.1f}k, up {change / 1000:,.1f}k on the day.\n"
        "Source: XVA system · as of 2026-11-19 · run XVA-20261119-01 · CAD thousands"
    )
    assert score_answer.score(q, good) == []
    bad = "Total XVA was about 5 million and rose a bit."
    assert len(score_answer.score(q, bad)) >= 3


def test_exec_leak_is_detected():
    q = QUESTIONS["Q07"]
    leaked = score_answer.expected_value(q["must_not_include_numbers"][0])
    answer = f"At desk level only. Northwind CVA is {leaked / 1000:,.1f}k."
    assert any(p.startswith("leaked") for p in score_answer.score(q, answer))


def test_every_golden_question_resolves_against_the_tools():
    for q in QUESTIONS.values():
        for spec in q.get("expect_numbers", []) + q.get("must_not_include_numbers", []):
            assert isinstance(score_answer.expected_value(spec), float), q["id"]
