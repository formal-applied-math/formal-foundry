"""Pure tests for the autop prove probe — injected check_fn, no daemon."""
from __future__ import annotations

import autop


def test_autop_candidate_replaces_by_sorry_with_tactic():
    stmt = "theorem t (x : ℝ) : x = x := by sorry"
    assert autop.autop_candidate(stmt, "nlinarith") == "theorem t (x : ℝ) : x = x := by nlinarith"


def test_autop_prove_returns_first_tactic_that_closes():
    calls = []

    def check(text):
        calls.append(text)
        ok = text.endswith("by nlinarith")
        return {"success": ok, "sorry_count": 0 if ok else 1, "errors": []}

    res = autop.autop_prove("theorem t : True := by sorry", check_fn=check,
                            menu=("simp", "nlinarith", "aesop"))
    assert res == {"tactic": "nlinarith",
                   "proof": "theorem t : True := by nlinarith"}
    assert len(calls) == 2   # stops at nlinarith, never tries aesop


def test_autop_prove_rejects_success_with_residual_sorry():
    # a tactic that "succeeds" but leaves a sorry is NOT a close
    def check(text):
        return {"success": True, "sorry_count": 1, "errors": []}

    assert autop.autop_prove("theorem t : True := by sorry", check_fn=check,
                             menu=("simp",)) is None


def test_autop_prove_returns_none_when_all_fail():
    def check(text):
        return {"success": False, "sorry_count": 1, "errors": ["boom"]}

    assert autop.autop_prove("theorem t : True := by sorry", check_fn=check) is None


def test_autop_prove_returns_none_when_statement_has_no_sorry():
    # a complete proof (no hole) must NOT be misattributed to a tactic that did nothing
    def check(text):
        return {"success": True, "sorry_count": 0, "errors": []}
    assert autop.autop_prove("theorem t : True := by trivial", check_fn=check) is None


def test_autop_prove_returns_none_when_multiple_sorries():
    def check(text):
        return {"success": True, "sorry_count": 0, "errors": []}
    stmt = "theorem a : True := by sorry\ntheorem b : True := by sorry"
    assert autop.autop_prove(stmt, check_fn=check) is None


def test_scout_rescue_none_when_author_passed():
    assert autop.scout_rescue(True, {"tactic": "simp", "proof": "P"}) is None


def test_scout_rescue_none_when_no_autop():
    assert autop.scout_rescue(False, None) is None


def test_scout_rescue_banks_scout_when_leanstral_missed_and_autop_hit():
    r = autop.scout_rescue(False, {"tactic": "nlinarith", "proof": "PROOF"})
    assert r == {"winning_candidate": "PROOF", "proof_source": "autop-nlinarith"}


def test_scout_rescue_author_pass_with_autop_available_is_still_author():
    # even if autop also closed it, an author pass wins → NOT a scout
    assert autop.scout_rescue(True, {"tactic": "simp", "proof": "X"}) is None
