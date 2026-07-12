"""Tests for the issue->stub autoformalizer sub-probe. Pure logic only —
injected chat_fn/check_fn, temp trees; no Lean, no API, no network."""

from __future__ import annotations

import build_manifest as bm

import autoformalize as af


# --- split_statement ---------------------------------------------------------

def test_split_statement_simple():
    name, binders, concl = af.split_statement("theorem foo (x : ℝ) : x = x := by sorry")
    assert name == "foo"
    assert binders.strip() == "(x : ℝ)"
    assert concl.strip() == "x = x"


def test_split_statement_no_binders():
    name, binders, concl = af.split_statement("theorem foo : True := by sorry")
    assert name == "foo"
    assert binders.strip() == ""
    assert concl.strip() == "True"


def test_split_statement_forall_colon_stays_in_concl():
    # the FIRST depth-0 colon is the type separator; the ∀'s colon is later, so
    # it stays inside the conclusion.
    name, binders, concl = af.split_statement("theorem foo : ∀ x : ℝ, x = x := by sorry")
    assert name == "foo"
    assert binders.strip() == ""
    assert concl.strip() == "∀ x : ℝ, x = x"


def test_split_statement_fra_like_multigroup_multiline():
    stub = (
        "theorem fra {P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0) :\n"
        "    P₁ = P₂ * (1 + δ) ∧ (δ = 0 ↔ P₁ = P₂) := by sorry"
    )
    name, binders, concl = af.split_statement(stub)
    assert name == "fra"
    assert "{P₁ P₂ δ : ℝ}" in binders
    assert "hP₂" in binders and "hδ" in binders
    assert "∧" in concl
    assert ":=" not in concl and "sorry" not in concl


def test_split_statement_ignores_leading_module_scaffold():
    stub = (
        "module\npublic import Mathlib\n-- pointers: A.lean\n"
        "/-! doc: a : b -/\nnamespace MathFin\n"
        "theorem bar (h : p) : q := by sorry\nend MathFin\n"
    )
    name, binders, concl = af.split_statement(stub)
    assert name == "bar"
    assert binders.strip() == "(h : p)"
    assert concl.strip() == "q"


# --- explicit_arg_names ------------------------------------------------------

def test_explicit_arg_names_only_parenthesized():
    # explicit () binders are passed to the re-export; {} implicit and [] instance
    # are inferred, so they are NOT passed.
    binders = "{P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0)"
    assert af.explicit_arg_names(binders) == ["hP₂", "hδ"]


def test_explicit_arg_names_multiple_in_one_group():
    assert af.explicit_arg_names("(a b : ℝ) (h : a = b)") == ["a", "b", "h"]


def test_explicit_arg_names_none():
    assert af.explicit_arg_names("{x : ℝ}") == []


# --- section mapping ---------------------------------------------------------

def test_section_for_area_known():
    assert af.section_for_area("fixed-income") == "FixedIncome"
    assert af.section_for_area("actuarial") == "Actuarial"
    assert af.section_for_area("credit") == "FixedIncome"   # credit lives in FixedIncome


def test_section_for_area_new_and_unknown():
    assert af.section_for_area("fx") == "FX"                # new dir, umbrella absorbs
    assert af.section_for_area("stoch-vol") == "StochVol"   # unknown → CamelCase fallback


# --- emit_target_files -------------------------------------------------------

_ISSUE = {
    "number": 67,
    "area": "fixed-income",
    "title": "FRA value + fair rate",
    "difficulty": "good-first",
    "pointers": ["MathFin/FixedIncome/ForwardRate.lean", "MathFin/FixedIncome/ZCB.lean"],
}
_STUB = ("theorem fra_value {P₁ P₂ δ : ℝ} (hP₂ : 0 < P₂) (hδ : δ ≠ 0) :\n"
         "    P₁ = P₂ * (1 + δ) := by sorry")
_META = {"module_name": "FRA", "benchmark_id": "mf-fi-fra",
         "docstring": "FRA value and fair simple forward rate."}


def test_emit_stub_headers_and_scaffold():
    lean_text, _entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "-- source-issue: 67" in lean_text
    assert "-- main-module: MathFin/FixedIncome/FRA.lean" in lean_text
    assert "-- benchmark: benchmarks/mathematical_finance.json" in lean_text
    assert "-- benchmark-id: mf-fi-fra" in lean_text
    assert ("-- pointers: MathFin/FixedIncome/ForwardRate.lean, "
            "MathFin/FixedIncome/ZCB.lean") in lean_text
    assert "@[expose] public section" in lean_text
    assert "namespace MathFin" in lean_text and "end MathFin" in lean_text
    assert "theorem fra_value" in lean_text
    assert "FRA value and fair simple forward rate." in lean_text
    assert lean_text.count("sorry") == 1        # exactly one — build_manifest requires it


def test_emit_stub_imports_pointer_modules():
    # coherence-first: the stub imports its pointer modules so a drafted statement
    # can consume existing MathFin defs, not just Mathlib.
    lean_text, _entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert "public import Mathlib" in lean_text
    assert "public import MathFin.FixedIncome.ForwardRate" in lean_text
    assert "public import MathFin.FixedIncome.ZCB" in lean_text


def test_emit_stub_roundtrips_through_build_manifest():
    lean_text, _entry, placement = af.emit_target_files(_ISSUE, _STUB, _META)
    # the real consumer parses the placement headers + the decl the same way
    meta = bm.parse_meta(lean_text)
    assert meta["main_module"] == "MathFin/FixedIncome/FRA.lean"
    assert meta["benchmark"] == "benchmarks/mathematical_finance.json"
    assert meta["benchmark_id"] == "mf-fi-fra"
    assert meta["source_issue"] == 67
    assert bm.parse_pointers(lean_text) == _ISSUE["pointers"]
    name, _b, _c = af.split_statement(lean_text)
    assert name == "fra_value"


def test_emit_entry_reexport_and_provenance():
    _lean, entry, _placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert entry["id"] == "mf-fi-fra"
    assert entry["domain"] == "mathematical_finance"
    assert entry["metadata"]["formalization_status"] == "full"
    assert entry["metadata"]["provenance"] == {
        "statement_source": "magistral-autoform",
        "statement_model": "magistral-medium",
        "source": "leanstral-autoform",
        "model": "labs-leanstral-1-5",
        "issue": 67,
    }
    code = entry["code"]["lean"]
    assert "import MathFin.FixedIncome.FRA" in code
    assert "theorem mf_fi_fra" in code                  # dashes -> underscores
    assert "MathFin.fra_value hP₂ hδ" in code           # applies the module lemma, explicit args only
    assert "sorry" not in code                          # a re-export, not a proof


def test_emit_placement_dict():
    _lean, _entry, placement = af.emit_target_files(_ISSUE, _STUB, _META)
    assert placement == {
        "main_module": "MathFin/FixedIncome/FRA.lean",
        "benchmark": "benchmarks/mathematical_finance.json",
        "benchmark_id": "mf-fi-fra",
        "source_issue": 67,
    }
