"""Pure tests for the acceptance gate — injected check_fn, no daemon/Lean."""
from __future__ import annotations

import gate

CLEAN = "import Mathlib\ntheorem t : True := trivial"


def test_gate_passes_when_clean_and_axiom_clean():
    calls = []

    def check(code):
        calls.append(code)
        return {"success": True, "sorry_count": 0, "errors": []}

    r = gate.gate(CLEAN, "t", check_fn=check)
    assert r["passed"] is True
    assert r["axioms_clean"] is True
    assert len(calls) == 2  # candidate check + axiom-guard check


def test_gate_rejects_forbidden_before_touching_daemon():
    bad = "import Mathlib\ntheorem t : True := by sorry"
    calls = []
    r = gate.gate(bad, "t", check_fn=lambda c: calls.append(c) or {"success": True})
    assert r["passed"] is False
    assert r["reason"].startswith("forbidden")
    assert calls == []  # slop screen fires first; the daemon is never hit


def test_gate_rejects_lint_dirty_before_touching_daemon():
    # kernel-green but lint-dirty (snake_case def, no docstring) — the class that
    # opened autoform PR #123 red on the main repo's `lake lint`
    bad = "def payer_swap_value (x : ℝ) : ℝ := x\ntheorem t : True := trivial"
    calls = []
    r = gate.gate(bad, "t", check_fn=lambda c: calls.append(c) or {"success": True})
    assert r["passed"] is False
    assert r["reason"].startswith("lint:")
    assert calls == []  # textual screen — the daemon is never hit


def test_gate_rejects_compile_failure():
    r = gate.gate(CLEAN, "t",
                  check_fn=lambda c: {"success": False, "sorry_count": 0, "errors": ["boom"]})
    assert r["passed"] is False
    assert r["reason"] == "compile_or_sorry"
    assert r["errors"] == ["boom"]


def test_gate_rejects_residual_sorry_even_if_success():
    r = gate.gate(CLEAN, "t",
                  check_fn=lambda c: {"success": True, "sorry_count": 1, "errors": []})
    assert r["passed"] is False
    assert r["reason"] == "compile_or_sorry"


def test_gate_rejects_axiom_dirty():
    # candidate compiles clean (1st check ok) but the axiom-guard block fails (2nd check)
    seq = iter([{"success": True, "sorry_count": 0, "errors": []},
                {"success": False, "sorry_count": 0, "errors": []}])
    r = gate.gate(CLEAN, "t", check_fn=lambda c: next(seq))
    assert r["passed"] is False
    assert r["reason"] == "axiom_dirty"
    assert r["axioms_clean"] is False
