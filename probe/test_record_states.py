"""Tests for the vibe harness's proof-state recording hook. No daemon, no API."""
from __future__ import annotations

import json

import state_cache as sc
import vibe_prove


def _check(goals):
    """Daemon stub: goal keyed by how many tactic lines precede the spliced `sorry`."""
    def check(code: str) -> dict:
        body = code.split(":= by", 1)[1] if ":= by" in code else ""
        n = len([ln for ln in body.splitlines()
                 if ln.strip() and ln.strip() != "sorry"])
        goal = goals.get(n)
        if goal is None:
            return {"success": False, "errors": ["nope"], "sorries": []}
        return {"success": False, "sorry_count": 1, "sorries": [{"goal": goal}]}
    return check


def test_records_states_into_the_cache_and_reports_counts(tmp_path):
    cache = sc.StateCache(str(tmp_path / "c.json"))
    cand = "theorem t : P := by\n  intro x\n  simp"
    got = vibe_prove.record_proof_states(
        cand, target_id="cal-bk-1", check_fn=_check({0: "⊢ P", 1: "x : X\n⊢ Q"}),
        cache=cache, log_path=str(tmp_path / "states.jsonl"))
    assert got == {"states": 2, "new_states": 2}
    assert cache.report()["distinct_states"] == 2


def test_second_target_hitting_the_same_states_is_new_states_zero(tmp_path):
    """The measurement in one line: the same goal reached by a DIFFERENT target is
    exactly the cross-target recurrence the cache exists to exploit."""
    cache = sc.StateCache(str(tmp_path / "c.json"))
    cand = "theorem t : P := by\n  intro x\n  simp"
    check = _check({0: "⊢ P", 1: "x : X\n⊢ Q"})
    log = str(tmp_path / "states.jsonl")
    vibe_prove.record_proof_states(cand, target_id="t1", check_fn=check, cache=cache,
                                   log_path=log)
    got = vibe_prove.record_proof_states(cand, target_id="t2", check_fn=check, cache=cache,
                                         log_path=log)
    assert got == {"states": 2, "new_states": 0}
    assert cache.report()["cross_target_states"] == 2


def test_every_sighting_is_logged_as_jsonl_for_offline_measurement(tmp_path):
    cache = sc.StateCache(str(tmp_path / "c.json"))
    log = str(tmp_path / "states.jsonl")
    vibe_prove.record_proof_states(
        "theorem t : P := by\n  simp", target_id="cal-bk-9",
        check_fn=_check({0: "⊢ P"}), cache=cache, log_path=log)
    rows = [json.loads(line) for line in open(log, encoding="utf-8")]
    assert len(rows) == 1
    assert rows[0]["target"] == "cal-bk-9" and rows[0]["tactic"] == "simp"
    assert rows[0]["key"] and rows[0]["state"] == "⊢ P"


def test_a_dead_daemon_records_nothing_and_does_not_raise(tmp_path):
    """Recording runs AFTER the proof already passed its gate. It must never turn a
    green target red, so an infra failure degrades to zero states."""
    cache = sc.StateCache(str(tmp_path / "c.json"))

    def dead(_code):
        return {"success": False, "error": "daemon check did not complete", "errors": ["x"]}

    got = vibe_prove.record_proof_states(
        "theorem t : P := by\n  simp", target_id="t", check_fn=dead, cache=cache,
        log_path=str(tmp_path / "s.jsonl"))
    assert got == {"states": 0, "new_states": 0}


def test_a_raising_check_fn_is_swallowed(tmp_path):
    cache = sc.StateCache(str(tmp_path / "c.json"))

    def boom(_code):
        raise RuntimeError("socket exploded")

    got = vibe_prove.record_proof_states(
        "theorem t : P := by\n  simp", target_id="t", check_fn=boom, cache=cache,
        log_path=str(tmp_path / "s.jsonl"))
    assert got == {"states": 0, "new_states": 0}


# --- consumption: the suggestions reach the prover's task -------------------

def test_the_vibe_task_carries_cross_target_suggestions_when_present():
    task = vibe_prove.build_vibe_task("f.lean", "t", "sig : …",
                                      state_hints="-- goal:\n⊢ B\n-- closed by: ring")
    assert "⊢ B" in task and "ring" in task


def test_the_vibe_task_is_unchanged_when_there_are_no_suggestions():
    plain = vibe_prove.build_vibe_task("f.lean", "t", "sig : …")
    assert plain == vibe_prove.build_vibe_task("f.lean", "t", "sig : …", state_hints="")
