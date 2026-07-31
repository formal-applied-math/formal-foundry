"""Tests for the necessity prober (backlog R). Pure: injected check_fn/prove_fn/
regate_fn, no Lean, no daemon, no network.

The case these exist for is the one the warning-driven `strengthen_candidate` cannot
see: a hypothesis the proof *uses* but the theorem does not *need*."""
from __future__ import annotations

import strengthen as S


# Verbatim shape from formal-mathfin#163: `h` IS used (`h.le`), so no unused-variable
# warning fires and the existing strip pass leaves it alone — yet `div_nonneg` needs
# only `0 ≤` on both legs, which `Finset.sum_nonneg` gives for free.
_GUARDED = """theorem gainToPain_nonneg_of_pain_pos {a : Type*} (S : Finset a) (r : a → ℝ)
    (h : 0 < ∑ s ∈ S, max (-(r s)) 0) : 0 ≤ gainToPain S r := by
  have hnum : 0 ≤ ∑ s ∈ S, max (r s) 0 := Finset.sum_nonneg fun s _ => le_max_right _ _
  have hden : 0 ≤ ∑ s ∈ S, max (-(r s)) 0 := h.le
  exact div_nonneg hnum hden
"""

_REPROVED = """theorem gainToPain_nonneg_of_pain_pos {a : Type*} (S : Finset a) (r : a → ℝ)
    : 0 ≤ gainToPain S r := by
  exact div_nonneg (Finset.sum_nonneg fun s _ => le_max_right _ _)
    (Finset.sum_nonneg fun s _ => le_max_right _ _)
"""

_NAME = "gainToPain_nonneg_of_pain_pos"
_OK = {"errors": [], "sorry_count": 0}
_PASS = {"passed": True}


def _elaborates(code: str) -> dict:
    """Statement-level check standing in for the daemon: the reduced statement is
    well-formed unless a name it dropped is still referenced by a surviving binder
    or by the conclusion. That is what makes `S`, `r` and `a` cost zero prover
    tokens while `h` reaches the prover."""
    import re
    from autoformalize import _binder_groups, _locate_named
    try:
        b, sep, _e = _locate_named(code, _NAME)
    except ValueError:
        return {"errors": ["decl not found"], "sorry_count": 0}
    bound = {"a"} if "{a : Type*}" in code else set()
    for _s, _en, _op, names in _binder_groups(code[b:sep]):
        bound.update(names or [])
    gone = {"a", "S", "r", "h"} - bound
    used = set(re.findall(r"[A-Za-z_][A-Za-z0-9_']*", code[b:sep] + code[sep:]))
    if gone & used:
        return {"errors": [f"unknown identifier '{sorted(gone & used)[0]}'"],
                "sorry_count": 0}
    return {"errors": [], "sorry_count": 1 if "sorry" in code else 0}


def test_probe_drops_the_binder_and_blanks_the_proof():
    p = S.necessity_probe(_GUARDED, _NAME, {"h"})
    assert "(h : 0 < ∑ s ∈ S, max (-(r s)) 0)" not in p
    assert "(S : Finset a)" in p and "{a : Type*}" in p
    assert p.rstrip().endswith("sorry")
    assert "div_nonneg" not in p          # the old proof is gone, not reused


def test_probe_is_none_when_nothing_matches():
    assert S.necessity_probe(_GUARDED, _NAME, {"nosuchbinder"}) is None
    assert S.necessity_probe(_GUARDED, "no_such_theorem", {"h"}) is None


def test_free_filter_offers_the_guard():
    assert S.droppable_hypotheses(_GUARDED, _NAME, check_fn=_elaborates) == ["h"]


def test_free_filter_skips_implicit_and_dependency_binders():
    # `{a : Type*}` is implicit so it is never a hypothesis; `S`/`r` are explicit but
    # the conclusion needs them, so they must not reach the prover
    got = S.droppable_hypotheses(_GUARDED, _NAME, check_fn=_elaborates)
    assert "a" not in got and "S" not in got and "r" not in got


def test_free_filter_costs_nothing_on_daemon_trouble():
    assert S.droppable_hypotheses(_GUARDED, _NAME,
                                  check_fn=lambda c: {"error": "wedged"}) == []


def test_reproving_without_the_guard_strengthens_the_theorem():
    calls = []

    def prove(code: str) -> dict:
        calls.append(code)
        return {"lean_text": _REPROVED, "tokens": 120}

    r = S.unnecessary_hypotheses(_GUARDED, _NAME, check_fn=_elaborates,
                                 prove_fn=prove, regate_fn=lambda c: _PASS)
    assert r["dropped"] == ["h"] and r["changed"] is True
    assert "0 < ∑" not in r["candidate"]
    assert r["tokens"] == 120
    assert len(calls) == 1               # only the binder that passed the free filter


def test_a_load_bearing_hypothesis_is_kept_when_the_prover_cannot_close_it():
    r = S.unnecessary_hypotheses(
        _GUARDED, _NAME, check_fn=_elaborates,
        prove_fn=lambda c: {"lean_text": c, "tokens": 90},   # still carries the sorry
        regate_fn=lambda c: _PASS)
    assert r["dropped"] == [] and r["candidate"] == _GUARDED


def test_a_red_regate_reverts_the_drop():
    # the prover "closed" it but the full battery disagrees — keep the proved original
    r = S.unnecessary_hypotheses(
        _GUARDED, _NAME, check_fn=_elaborates,
        prove_fn=lambda c: {"lean_text": _REPROVED, "tokens": 10},
        regate_fn=lambda c: {"passed": False, "reason": "axioms"})
    assert r["dropped"] == [] and r["candidate"] == _GUARDED


def test_prover_infra_failure_never_loses_the_proof():
    def boom(code: str) -> dict:
        raise RuntimeError("endpoint 502")

    r = S.unnecessary_hypotheses(_GUARDED, _NAME, check_fn=_elaborates,
                                 prove_fn=boom, regate_fn=lambda c: _PASS)
    assert r["candidate"] == _GUARDED and r["changed"] is False


def test_refuses_to_judge_a_candidate_with_a_sorry():
    stub = "theorem foo (h : p) : q := by sorry\n"
    r = S.unnecessary_hypotheses(stub, "foo", check_fn=_elaborates,
                                 prove_fn=lambda c: {"lean_text": "x", "tokens": 5},
                                 regate_fn=lambda c: _PASS)
    assert r["changed"] is False and r["tokens"] == 0


def test_returned_candidate_is_always_a_regated_state():
    seen = []
    r = S.unnecessary_hypotheses(
        _GUARDED, _NAME, check_fn=_elaborates,
        prove_fn=lambda c: {"lean_text": _REPROVED, "tokens": 1},
        regate_fn=lambda c: (seen.append(c), _PASS)[1])
    assert r["candidate"] in seen        # exactly the text the gate signed off on


def test_tactic_sweep_closes_with_the_first_tactic_that_works():
    seen = []

    def check(code: str) -> dict:
        seen.append(code)
        ok = "positivity" in code and "unfold" not in code
        return {"errors": ([] if ok else ["failed"]), "sorry_count": 0}

    prove = S.tactic_sweep_prover(check, def_names=("gainToPain",))
    out = prove("theorem foo : 0 <= gainToPain S r := by sorry\n")
    assert "positivity" in out["lean_text"] and "sorry" not in out["lean_text"]
    assert out["tokens"] == 0                     # a sweep is not a prover call


def test_tactic_sweep_returns_the_probe_when_nothing_closes():
    probe = "theorem foo : p := by sorry\n"
    prove = S.tactic_sweep_prover(lambda c: {"errors": ["nope"], "sorry_count": 0})
    assert prove(probe)["lean_text"] == probe


def test_tactic_sweep_skips_def_slots_when_there_are_no_defs():
    tried = []
    prove = S.tactic_sweep_prover(
        lambda c: (tried.append(c), {"errors": ["nope"], "sorry_count": 0})[1])
    prove("theorem foo : p := by sorry\n")
    assert not any("{defs}" in t or "{unfold}" in t for t in tried)
    assert all("simp []" not in t for t in tried)   # no empty-simp-list garbage


def test_tactic_sweep_stops_on_daemon_error():
    calls = []
    prove = S.tactic_sweep_prover(
        lambda c: (calls.append(c), {"error": "wedged"})[1], def_names=("f",))
    probe = "theorem foo : p := by sorry\n"
    assert prove(probe)["lean_text"] == probe
    assert len(calls) == 1                        # bailed, did not grind the whole list
