"""Tests for the obstruction-family triage (Task 1.7). Pure aggregation over
fixture rows — no Lean, no API, no network."""

from __future__ import annotations

import json

from obstructions import (FAMILIES, bucket_obstructions, census, load_rows,
                          render_report)


def test_bucketing_families():
    refill = [
        # depth wins over the later unknown-id (routing evidence precedence): #53's
        # attempt 1 hit depth, attempt 2 hallucinated MathFin.zcb.
        {"issue": 53, "outcome": "formalize", "history": [
            {"attempt": 1, "gate": "depth", "detail": "depth-gate: ..."},
            {"attempt": 2, "gate": "formalize", "unknown_identifiers": ["MathFin.zcb"]}]},
        # a guessed constant that survived retrieval, no depth signal → its own family
        {"issue": 88, "outcome": "formalize", "history": [
            {"attempt": 1, "gate": "formalize", "unknown_identifiers": ["MathFin.omegaRatio"]}]},
        {"issue": 61, "outcome": "formalize", "history": [{"attempt": 1, "gate": "formalize"}]},
        {"issue": 73, "outcome": "unfaithful", "history": [{"attempt": 1, "gate": "unfaithful"}]},
        {"issue": 90, "outcome": "indeterminate", "history": [{"attempt": 1, "gate": "indeterminate"}]},
        {"issue": 66, "outcome": "seeded", "history": []},   # not an obstruction
    ]
    summary = [
        {"target": "cal-bk-67", "outcome": "pass"},          # not an obstruction
        {"target": "cal-bk-80", "outcome": "max_rounds"},
    ]
    b = bucket_obstructions(refill, summary)
    assert b["depth-gate"]["count"] == 1 and "53" in b["depth-gate"]["issues"]
    assert b["unknown-id-despite-retrieval"]["count"] == 1 and "88" in b["unknown-id-despite-retrieval"]["issues"]
    assert b["no-elaborating-draft"]["count"] == 1 and "61" in b["no-elaborating-draft"]["issues"]
    assert b["gate-fail"]["count"] == 1 and "73" in b["gate-fail"]["issues"]
    assert b["infra-indeterminate"]["count"] == 1 and "90" in b["infra-indeterminate"]["issues"]
    assert b["prover-max-rounds"]["count"] == 1 and "cal-bk-80" in b["prover-max-rounds"]["issues"]
    # seeded + pass contribute nothing
    assert sum(x["count"] for x in b.values()) == 6


def test_render_report_has_families_and_trend():
    b = bucket_obstructions([{"issue": 53, "outcome": "depth", "history": [{"gate": "depth"}]}], [])
    prev = {f: {"count": 0, "issues": {}} for f in FAMILIES}
    prev["depth-gate"]["count"] = 3
    md = render_report(b, prev=prev)
    assert "Obstruction families" in md
    assert "depth-gate" in md
    assert "▼" in md   # 1 now vs 3 last tick → down-trend


def test_load_rows_missing_dir_is_empty(tmp_path):
    refill, summary = load_rows(str(tmp_path))   # nothing there
    assert refill == [] and summary == []


def test_load_rows_reads_record_level_rows_via_provenance(tmp_path):
    # load_rows now sources its raw records from the provenance substrate, but keeps the
    # RECORD grain (full history) that the record-level family classifier needs.
    import json
    (tmp_path / "refill-history.jsonl").write_text(
        json.dumps({"issue": 53, "outcome": "formalize",
                    "history": [{"attempt": 1, "gate": "depth"},
                                {"attempt": 2, "gate": "formalize"}]}) + "\n", encoding="utf-8")
    (tmp_path / "pipeline-x-summary.jsonl").write_text(
        json.dumps({"target": "cal-bk-80", "outcome": "max_rounds"}) + "\n", encoding="utf-8")
    refill, summary = load_rows(str(tmp_path))
    assert len(refill) == 1 and len(refill[0]["history"]) == 2   # whole history preserved
    b = bucket_obstructions(refill, summary)
    assert b["depth-gate"]["count"] == 1        # depth-anywhere precedence still works
    assert b["prover-max-rounds"]["count"] == 1


# --- the deep wiring: the census computed off the normalized substrate ---------

def _seed_runs(tmp_path):
    """A runs/ dir exercising every family + the tricky cases (depth-anywhere precedence,
    a seed WITH prior failed attempts, unknown-ids, prove max-rounds)."""
    (tmp_path / "refill-history.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"issue": 53, "ts": "t1", "outcome": "formalize", "history": [
            {"attempt": 1, "gate": "depth"},
            {"attempt": 2, "gate": "formalize", "unknown_identifiers": ["MathFin.zcb"]}]},
        {"issue": 88, "ts": "t2", "outcome": "formalize", "history": [
            {"attempt": 1, "gate": "formalize", "unknown_identifiers": ["MathFin.omega"]}]},
        {"issue": 66, "ts": "t3", "outcome": "seeded", "history": [
            {"attempt": 1, "gate": "formalize"}]},          # seeded AFTER a failed attempt
        {"issue": 73, "ts": "t4", "outcome": "unfaithful", "history": [
            {"attempt": 1, "gate": "unfaithful"}]},
        {"issue": 90, "ts": "t5", "outcome": "indeterminate", "history": [
            {"attempt": 1, "gate": "indeterminate"}]},
    ]) + "\n", encoding="utf-8")
    (tmp_path / "pipeline-x-summary.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"target": "cal-bk-80", "outcome": "max_rounds"},
        {"target": "cal-bk-67", "outcome": "pass"},         # not an obstruction
    ]) + "\n", encoding="utf-8")


def test_census_off_substrate_matches_raw_bucketing(tmp_path):
    _seed_runs(tmp_path)
    raw = bucket_obstructions(*load_rows(str(tmp_path)))     # the record-level reference
    sub = census(str(tmp_path))                              # computed off the substrate
    assert sub == raw                                        # byte-identical buckets
    assert sub["depth-gate"]["count"] == 1                   # depth beats the later unknown
    assert sub["unknown-id-despite-retrieval"]["count"] == 1
    assert sub["gate-fail"]["count"] == 1
    assert sub["infra-indeterminate"]["count"] == 1
    assert sub["prover-max-rounds"]["count"] == 1
    seen = {i for f in sub.values() for i in f["issues"]}
    assert "66" not in seen                                  # a seed (even after a failure) never counts


def test_census_ignores_the_ab_arm_log(tmp_path):
    # the census reads the *-summary prove stream, NOT the A/B decomposer log (which the
    # substrate also ingests) — so a decompose-arm failure does not inflate the census.
    _seed_runs(tmp_path)
    (tmp_path / "ab-decomposer.jsonl").write_text(
        json.dumps({"target": "cal-bk-77", "arm": "decompose", "outcome": "max_rounds",
                    "ts": "t9"}) + "\n", encoding="utf-8")
    sub = census(str(tmp_path))
    assert sub == bucket_obstructions(*load_rows(str(tmp_path)))
    assert "cal-bk-77" not in {i for f in sub.values() for i in f["issues"]}
