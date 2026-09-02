import json
import os
import tempfile

from house_context import (
    build_drafter_prompt,
    build_system_prompt,
    extract_signatures,
    read_patterns,
    read_pins,
)

import domain_pack

PACK = domain_pack.load("mathfin")


MAIN = "/home/rapha/code/automated_proofs_quantfin"


def _fake_main_repo(tmp_path):
    """Minimal main-repo layout: pins + a patterns.md carrying a 'Structural
    patterns' section (with prover tactics) and a 'Statement design' section."""
    repo = tmp_path
    (repo / "lean-toolchain").write_text("leanprover/lean4:v4.31.0\n")
    (repo / "lake-manifest.json").write_text(json.dumps({"packages": [
        {"name": "mathlib", "rev": "abc123def456ghijkl"},
        {"name": "brownianmotion", "rev": "d6f23da000011122"},
    ]}))
    docs = repo / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "patterns.md").write_text(
        "## Structural patterns\n\n"
        "reach for `nlinarith [sq_nonneg x]` on nonlinear goals; `grind` on algebra.\n\n"
        "## Statement design (for the formalizer / drafter) (2026-07-18)\n\n"
        "- Shape hard side-conditions to be inherited, not asserted.\n"
        "- Casts go outward around lattice/arith ops.\n\n"
        "## Repair table (compiler error -> fix)\n\n| a | b |\n|---|---|\n| x | y |\n",
        encoding="utf-8",
    )
    return repo


def test_doctrine_covers_values_and_coherence():
    # PACK.house_doctrine now carries only the foundry-specific doctrine (values gate +
    # coherence + output rules); the house idioms/patterns are injected LIVE from
    # docs/patterns.md by build_system_prompt (first-class, always current).
    d = PACK.house_doctrine
    assert "native_decide" in d and "propext" in d and "COMPLETE file" in d
    assert "CONSUME" in d and "loogle" in d
    assert "docs/patterns.md" in d          # points at the live, authoritative source


def test_read_patterns_reads_the_live_file():
    if not os.path.exists(MAIN):
        return  # host-only
    p = read_patterns(MAIN)
    assert "Structural patterns" in p       # a real heading in docs/patterns.md
    assert len(p) > 2000


def test_read_patterns_missing_is_empty_not_raise():
    with tempfile.TemporaryDirectory() as d:
        assert read_patterns(d) == ""       # no docs/patterns.md → empty, no raise


def test_system_prompt_injects_live_patterns():
    if not os.path.exists(MAIN):
        return  # host-only
    sp = build_system_prompt(MAIN, PACK)
    assert "docs/patterns.md" in sp
    # LIVE content from the file, not a hardcoded snapshot:
    assert "Structural patterns" in sp
    assert "grind" in sp and "nlinarith" in sp


def test_system_prompt_injects_live_pins():
    if not os.path.exists(MAIN):
        return  # host-only; skip where the main repo isn't checked out
    sp = build_system_prompt(MAIN, PACK)
    pins = read_pins(MAIN, PACK)
    assert "PINS" in sp
    assert pins["toolchain"] in sp
    assert pins["mathlib"] != "?" and pins["mathlib"] in sp
    assert "BrownianMotion" in sp


def test_extract_signatures_lists_decls_and_skips_missing():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "M"))
        with open(os.path.join(d, "M", "Foo.lean"), "w", encoding="utf-8") as f:
            f.write("theorem foo_bar : 1 = 1 := rfl\n"
                    "noncomputable def baz (x : ℝ) : ℝ := x\n"
                    "lemma qux : 2 = 2 := rfl\n")
        # index_dir points at an empty dir → no index → regex fallback
        pack = extract_signatures(d, ["M/Foo.lean", "M/DoesNotExist.lean"],
                                  index_dir=os.path.join(d, "noindex"))
        assert "theorem foo_bar" in pack
        assert "def baz" in pack
        assert "lemma qux" in pack
        assert "consume" in pack.lower()


def test_extract_signatures_prefers_index_when_available():
    with tempfile.TemporaryDirectory() as d:
        idx = os.path.join(d, "index")
        os.makedirs(idx)
        with open(os.path.join(idx, "types.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "MathFin.vasicekBondPrice",
                                "module": "MathFin.FixedIncome.VasicekBondPrice",
                                "type": "ℝ → ℝ → ℝ", "docString": "ZCB price.",
                                "allowCompletion": True}) + "\n")
        with open(os.path.join(idx, "tactics.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"module": "MathFin.FixedIncome.VasicekBondPrice",
                                "goals": [{"pp": "⊢ 0 < P"}], "ppTac": "positivity",
                                "kind": "k"}) + "\n")
        pack = extract_signatures(d, ["MathFin/FixedIncome/VasicekBondPrice.lean"],
                                  index_dir=idx)
        # real signature (type), docstring, and a house-style exemplar
        assert "vasicekBondPrice : ℝ → ℝ → ℝ" in pack
        assert "ZCB price." in pack
        assert "positivity" in pack and "TACTIC EXEMPLARS" in pack


def test_context_pack_includes_dependency_closure():
    with tempfile.TemporaryDirectory() as d:
        idx = os.path.join(d, "index")
        os.makedirs(idx)
        with open(os.path.join(idx, "types.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "MathFin.foo", "module": "MathFin.A",
                                "type": "ℝ → ℝ", "docString": None}) + "\n")
            f.write(json.dumps({"name": "MathFin.helper", "module": "MathFin.B",
                                "type": "ℝ → Prop", "docString": "the helper"}) + "\n")
        with open(os.path.join(idx, "const_dep.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"name": "MathFin.foo", "deps": ["MathFin.helper"]}) + "\n")
        pack = extract_signatures(d, ["MathFin/A.lean"], index_dir=idx)
        assert "foo : ℝ → ℝ" in pack                # pointer-module decl
        assert "helper : ℝ → Prop" in pack           # its cross-module dependency
        assert "the helper" in pack                  # closure carries the docstring too
        assert "DEPENDENCY-CLOSURE" in pack


def test_drafter_prompt_has_statement_design_and_pins_not_proof_tactics(tmp_path):
    # The DRAFTER writes STATEMENTS: it must get the pins + the statement-design
    # section of patterns.md, but NOT the prover's tactic ladder (which would only
    # tempt it to write proofs instead of a faithful statement).
    repo = _fake_main_repo(tmp_path)
    p = build_drafter_prompt(str(repo), PACK)
    assert "Statement design" in p and "leanprover/lean4:v4.31.0" in p
    assert "nlinarith" not in p  # prover-only tactic ladder stays out of the drafter prompt


def test_drafter_prompt_falls_back_when_no_statement_design_section(tmp_path):
    # Fail-open: patterns.md present but with no Statement-design header → the
    # curated fallback still gives the drafter its statement-design authority + pins.
    repo = _fake_main_repo(tmp_path)
    (repo / "docs" / "patterns.md").write_text("## Something else\n\nno design here.\n",
                                               encoding="utf-8")
    p = build_drafter_prompt(str(repo), PACK)
    assert "STATEMENT DESIGN" in p.upper()
    assert "leanprover/lean4:v4.31.0" in p
    assert "nlinarith" not in p
