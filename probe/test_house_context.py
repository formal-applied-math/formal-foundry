import json
import os
import tempfile

from house_context import (
    HOUSE_DOCTRINE,
    build_system_prompt,
    extract_signatures,
    read_pins,
)

MAIN = "/home/rapha/code/automated_proofs_quantfin"


def test_doctrine_covers_values_and_idioms():
    d = HOUSE_DOCTRINE
    # values gate
    assert "native_decide" in d and "propext" in d and "COMPLETE file" in d
    # coherence-first / anti-wrapper
    assert "CONSUME" in d and "loogle" in d
    # house idioms
    assert "grind" in d and "nlinarith" in d and "field_simp" in d
    assert "Real.exp (-(r * τ))" in d
    assert "This IS already that" in d
    # mathlib house-style golf (BM PR #484 maintainer review)
    assert "bare proof term" in d.lower()
    assert "reusable abstraction" in d.lower()
    assert "SigmaFiniteFiltration" in d


def test_system_prompt_injects_live_pins():
    if not os.path.exists(MAIN):
        return  # host-only; skip where the main repo isn't checked out
    sp = build_system_prompt(MAIN)
    pins = read_pins(MAIN)
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
