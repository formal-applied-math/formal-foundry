"""Emit hygiene (backlog V): the deterministic slop the #161/#162 drafts shipped."""
from __future__ import annotations

import autoformalize as af


def test_attribute_on_an_example_is_dropped():
    # #165 shipped `@[simp]` on two examples; examples are anonymous so it is inert
    stub = ("@[simp]\nexample : gainToPain s r = 0 := by simp\n\n"
            "theorem t : True := by sorry\n")
    out = af._prelint_stub(stub)
    assert "@[simp]" not in out
    assert "example : gainToPain s r = 0" in out    # the sanity check itself survives


def test_an_attribute_on_a_real_declaration_is_left_alone():
    stub = "@[simp]\nlemma foo : True := by trivial\n\ntheorem t : True := by sorry\n"
    out = af._prelint_stub(stub)
    assert "@[simp]" in out


def test_an_inferable_type_argument_becomes_implicit():
    # #165: `(S : Type*) (finset_S : Finset S)` forces `gainToPain S finset_S r`
    stub = ("noncomputable def gainToPain (S : Type*) (s : Finset S) (r : S -> Real) "
            ": Real := 0\n\ntheorem t : True := by sorry\n")
    out = af._prelint_stub(stub)
    assert "{S : Type*}" in out and "(S : Type*)" not in out
    assert "(s : Finset S)" in out                  # the rest of the signature is intact


def test_a_type_argument_nothing_mentions_stays_explicit():
    # not inferable, so making it implicit would break every call site
    stub = "theorem t (S : Type*) : True := by sorry\n"
    assert "(S : Type*)" in af._prelint_stub(stub)


def test_prelint_leaves_a_clean_stub_untouched():
    stub = ("noncomputable def upCapture {i : Type*} (up : Finset i) (p : i -> Real)"
            " : Real := 0\n\ntheorem t : True := by sorry\n")
    assert af._prelint_stub(stub) == af._prelint_stub(af._prelint_stub(stub))


# --- post-gate: prune the opens once the module has elaborated ----------------

_MOD = """module

public import Mathlib

@[expose] public section

namespace MathFin

open MeasureTheory ProbabilityTheory
open scoped NNReal ENNReal

theorem foo : True := trivial

end MathFin
"""


def _needs(*required):
    def check(code: str) -> dict:
        missing = [r for r in required if r not in code]
        if missing:
            return {"success": False, "errors": [f"unknown identifier ({missing[0]})"],
                    "sorry_count": 0}
        return {"success": True, "errors": [], "sorry_count": 0}
    return check


def test_unused_opens_are_trimmed():
    r = af.trim_unused_opens(_MOD, check_fn=_needs())
    assert "open MeasureTheory" not in r["candidate"]
    assert "open scoped NNReal" not in r["candidate"]
    assert len(r["removed"]) == 2
    assert "theorem foo" in r["candidate"]


def test_a_needed_open_is_kept():
    r = af.trim_unused_opens(_MOD, check_fn=_needs("open MeasureTheory ProbabilityTheory"))
    assert "open MeasureTheory ProbabilityTheory" in r["candidate"]
    assert r["removed"] == ["open scoped NNReal ENNReal"]


def test_trim_opens_is_fail_open_on_a_red_check():
    r = af.trim_unused_opens(_MOD, check_fn=lambda c: {"success": False, "errors": ["x"]})
    assert r["candidate"] == _MOD and r["removed"] == []
