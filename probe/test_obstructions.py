"""Tests for the obstruction-family triage (Task 1.7). Pure aggregation over
fixture rows — no Lean, no API, no network."""

from __future__ import annotations

from obstructions import FAMILIES, bucket_obstructions, load_rows, render_report


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
