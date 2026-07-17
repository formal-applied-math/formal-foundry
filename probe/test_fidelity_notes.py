"""Pure tests for the statement-fidelity notes renderer."""
from __future__ import annotations

import fidelity_notes as fn

STMT = "theorem foo (x : ℝ) : x + 0 = x := by simp"


def test_renders_id_statement_and_check_section():
    md = fn.render_notes(target_id="cal-x-1", lean_statement=STMT)
    assert "cal-x-1" in md
    assert "```lean" in md and "theorem foo" in md
    assert "## What to check" in md
    # no optional sections when their inputs are absent
    assert "Informal claim" not in md
    assert "pointers" not in md.lower() or "Existing results" not in md


def test_includes_issue_claim_pointers_and_provenance():
    md = fn.render_notes(
        target_id="cal-x-2", lean_statement=STMT, issue_number=108,
        issue_title="interest parity", issue_task="Prove covered interest parity.",
        pointers=["MathFin.zcb", "MathFin.forwardRate"],
        provenance={"source": "leanstral-autoform", "model": "labs-leanstral-1-5"})
    assert "#108" in md and "interest parity" in md
    assert "Prove covered interest parity." in md
    assert "`MathFin.zcb`" in md and "`MathFin.forwardRate`" in md
    assert "leanstral-autoform" in md and "labs-leanstral-1-5" in md
    assert "scout, not author" in md


def test_output_is_markdown_ending_in_single_newline():
    md = fn.render_notes(target_id="t", lean_statement=STMT)
    assert md.startswith("# Statement-fidelity notes")
    assert md.endswith("\n") and not md.endswith("\n\n")


def test_extract_statement_takes_signature_up_to_proof():
    code = "import Mathlib\n\ntheorem foo (x : ℝ) : x + 0 = x := by simp"
    assert fn.extract_statement(code) == "theorem foo (x : ℝ) : x + 0 = x"


def test_extract_statement_handles_attributes_and_modifiers():
    code = "@[simp] noncomputable def bar : ℕ := 3"
    assert fn.extract_statement(code) == "@[simp] noncomputable def bar : ℕ"


def test_extract_statement_falls_back_without_a_decl():
    assert fn.extract_statement("-- just a comment") == "-- just a comment"
