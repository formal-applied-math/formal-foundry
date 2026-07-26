"""Pure tests for the run-record provenance layer (item N) — temp jsonl, no daemon.

Derives the normalized (subject x attempt x stage) substrate from the existing
runs/*.jsonl telemetry into a versioned schema + a SQLite view; asserts the derivation
is faithful, schema-conformant, content-addressed, idempotent, and junk-tolerant.
"""
from __future__ import annotations

import json
import sqlite3

import provenance as pv


def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_draft_records_normalize_per_attempt(tmp_path):
    _write(tmp_path, "refill-history.jsonl", [
        {"ts": "2026-07-22T09:00:00", "issue": 62, "attempts": 2, "outcome": "formalize",
         "family": "undraftable", "arch": "routing-v1-2026-07-17",
         "history": [{"attempt": 1, "gate": "intent", "detail": "no defs"},
                     {"attempt": 2, "gate": "formalize", "detail": "no elaborating Lean"}]}])
    recs = [r for r in pv.iter_run_records(str(tmp_path)) if r["stage"] == "draft"]
    assert len(recs) == 2
    r1, r2 = sorted(recs, key=lambda r: r["attempt"])
    assert r1["issue"] == 62 and r1["attempt"] == 1 and r1["gate"] == "intent"
    assert r1["target"] == "cal-bk-62" and r1["arch"] == "routing-v1-2026-07-17"
    assert r2["gate"] == "formalize" and r2["family"] == "undraftable"
    assert r2["detail"] == "no elaborating Lean"


def test_draft_seed_with_empty_history_yields_a_record(tmp_path):
    # a seed on attempt 1 breaks the loop WITHOUT appending a history row (history=[]),
    # exactly like the cal-bk-9001 e2e — the substrate must still capture the seed.
    _write(tmp_path, "refill-history.jsonl", [
        {"ts": "t", "issue": 9001, "attempts": 1, "outcome": "seeded", "history": []}])
    recs = [r for r in pv.iter_run_records(str(tmp_path)) if r["stage"] == "draft"]
    assert len(recs) == 1
    assert recs[0]["outcome"] == "seeded" and recs[0]["attempt"] == 1
    assert recs[0]["gate"] is None


def test_prove_records_from_summary(tmp_path):
    _write(tmp_path, "pipeline-20260717-summary.jsonl", [
        {"target": "cal-bk-88", "outcome": "pass", "model": "labs-leanstral-1-5",
         "tokens": 1234, "harness": "vibe", "ts": "2026-07-17T19:00:00", "rounds": 3}])
    recs = [r for r in pv.iter_run_records(str(tmp_path)) if r["stage"] == "prove"]
    assert len(recs) == 1
    r = recs[0]
    assert r["target"] == "cal-bk-88" and r["issue"] == 88
    assert r["outcome"] == "pass" and r["model"] == "labs-leanstral-1-5"
    assert r["tokens"] == 1234 and r["attempt"] == 3 and r["harness"] == "vibe"


def test_record_id_stable_and_distinct():
    a = {"stage": "draft", "issue": 1, "target": "cal-bk-1", "attempt": 1,
         "outcome": "intent", "gate": "intent", "ts": "t", "source_file": "h.jsonl"}
    assert pv.record_id(a) == pv.record_id(dict(a))          # stable
    assert pv.record_id(a) != pv.record_id(dict(a, attempt=2))  # attempt distinguishes
    assert pv.record_id(a) != pv.record_id(dict(a, stage="prove"))


def test_records_conform_to_versioned_schema(tmp_path):
    _write(tmp_path, "refill-history.jsonl", [
        {"ts": "t", "issue": 5, "outcome": "depth", "arch": "routing-v1-2026-07-17",
         "history": [{"attempt": 1, "gate": "depth"}]}])
    _write(tmp_path, "pipeline-x-summary.jsonl", [
        {"target": "cal-bk-5", "outcome": "pass", "ts": "t"}])
    schema = pv.load_schema()
    required, props = set(schema["required"]), set(schema["properties"])
    recs = pv.iter_run_records(str(tmp_path))
    assert recs
    for r in recs:
        assert required <= set(r)      # every required field present
        assert set(r) <= props         # no stray fields beyond the schema
        assert r["stage"] in ("draft", "prove")


def test_build_sqlite_idempotent_and_dedup(tmp_path):
    _write(tmp_path, "refill-history.jsonl", [
        {"ts": "t", "issue": 5, "outcome": "depth", "arch": "A",
         "history": [{"attempt": 1, "gate": "depth"}]}])
    db = str(tmp_path / "prov.db")
    n1 = pv.build_sqlite(str(tmp_path), db)
    n2 = pv.build_sqlite(str(tmp_path), db)   # rebuild is derived, not appended
    assert n1 == n2 == 1
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT stage, issue, gate FROM run_record").fetchall() == [("draft", 5, "depth")]
    conn.close()


def test_tolerates_junk_and_absent(tmp_path):
    (tmp_path / "refill-history.jsonl").write_text(
        'not-json\n{"issue": 7, "ts": "t", "history": [{"attempt": 1, "gate": "formalize"}]}\n',
        encoding="utf-8")
    assert [r["issue"] for r in pv.iter_run_records(str(tmp_path))] == [7]
    assert pv.iter_run_records(str(tmp_path / "nope")) == []   # absent dir → empty, no raise
