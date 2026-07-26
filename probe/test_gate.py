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


def test_gate_surfaces_candidate_warnings():
    # the strengthen pass reads `unused variable` warnings off the gate result —
    # they come from the CANDIDATE check, not the axiom-guard check
    seq = iter([{"success": True, "sorry_count": 0, "errors": [],
                 "warnings": ["unused variable `hσ_eq`"]},
                {"success": True, "sorry_count": 0, "errors": [], "warnings": ["guard noise"]}])
    r = gate.gate(CLEAN, "t", check_fn=lambda c: next(seq))
    assert r["passed"] is True
    assert r["warnings"] == ["unused variable `hσ_eq`"]


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


# --- statement-integrity pin (item J) ----------------------------------------
# The kernel bar accepts whatever FILE the prover returns. A prover that (against
# instruction) weakens the theorem to something trivially provable would pass every
# check above. When the ORIGINAL stub `statement` is supplied, the accepted candidate
# must still assert the SAME signature (binders + conclusion) for `sorry_name`.

_STUB = "import Mathlib\ntheorem t (h : p) : q := by sorry"
_OK = lambda c: {"success": True, "sorry_count": 0, "errors": []}


def test_gate_statement_pin_passes_when_signature_preserved():
    proved = "import Mathlib\ntheorem t (h : p) : q := h"   # same signature, real proof
    r = gate.gate(proved, "t", check_fn=_OK, statement=_STUB)
    assert r["passed"] is True
    assert r["reason"] == "ok"


def test_gate_statement_pin_rejects_weakened_conclusion():
    weak = "import Mathlib\ntheorem t (h : p) : True := trivial"   # q → True
    r = gate.gate(weak, "t", check_fn=_OK, statement=_STUB)
    assert r["passed"] is False
    assert r["reason"] == "statement_altered"


def test_gate_statement_pin_rejects_dropped_binder():
    dropped = "import Mathlib\ntheorem t : q := hq"   # (h : p) removed
    r = gate.gate(dropped, "t", check_fn=_OK, statement=_STUB)
    assert r["passed"] is False
    assert r["reason"] == "statement_altered"


def test_gate_statement_pin_rejects_rename():
    renamed = "import Mathlib\ntheorem s (h : p) : q := h"   # not `t` anymore
    r = gate.gate(renamed, "s", check_fn=_OK, statement=_STUB)
    assert r["passed"] is False
    assert r["reason"] == "statement_altered"


def test_gate_statement_pin_is_whitespace_insensitive():
    reflowed = "import Mathlib\ntheorem t   (h : p)  :  q := h"   # same sig, extra spaces
    r = gate.gate(reflowed, "t", check_fn=_OK, statement=_STUB)
    assert r["passed"] is True


def test_gate_no_statement_pin_by_default():
    # backward-compat: without `statement`, the raw kernel bar (no pin) is unchanged —
    # the strengthen/trim/golf re-gates rely on this (they DELIBERATELY alter binders).
    weak = "import Mathlib\ntheorem t (h : p) : True := trivial"
    r = gate.gate(weak, "t", check_fn=_OK)
    assert r["passed"] is True
