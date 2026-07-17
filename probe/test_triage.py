"""Pure tests for the obstruction-family triage."""
from __future__ import annotations

import triage


def test_classify_covers_the_outcome_vocabulary():
    assert triage.classify({"outcome": "pass"}) == "solved"
    assert triage.classify({"outcome": "error"}) == "infra"
    assert triage.classify({"outcome": "max_rounds"}) == "prover_gave_up"
    assert triage.classify({"outcome": "budget_exhausted"}) == "budget_exhausted"
    assert triage.classify({"outcome": "fail_gate", "gate_reason": "forbidden:['sorry']"}) \
        == "used_forbidden_tactic"
    assert triage.classify({"outcome": "fail_gate", "gate_reason": "axiom_dirty"}) == "axiom_dirty"
    assert triage.classify({"outcome": "fail_gate", "gate_reason": "compile_or_sorry"}) \
        == "doesnt_compile"
    assert triage.classify({"outcome": "whatever"}) == "unknown:whatever"


def test_triage_counts_families():
    rows = [{"outcome": "pass"}, {"outcome": "pass"}, {"outcome": "error"},
            {"outcome": "fail_gate", "gate_reason": "compile_or_sorry"}]
    c = triage.triage(rows)
    assert c["solved"] == 2 and c["infra"] == 1 and c["doesnt_compile"] == 1


def test_load_summary_parses_and_skips_blank_and_malformed(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text('{"outcome":"pass"}\n\nnot-json\n{"outcome":"error"}\n', encoding="utf-8")
    assert [r["outcome"] for r in triage.load_summary(str(p))] == ["pass", "error"]


def test_render_lists_families_and_count():
    rows = [{"outcome": "pass"}, {"outcome": "error"}]
    out = triage.render(triage.triage(rows), rows)
    assert "triage of 2 target(s)" in out and "solved" in out and "infra" in out
