"""Tests for the lean_scout index adapter (scout_index)."""

import json
import os
import tempfile

from scout_index import ScoutIndex, default_index_dir, path_to_module


def test_path_to_module_handles_both_forms():
    assert (path_to_module("MathFin/FixedIncome/VasicekBondPrice.lean")
            == "MathFin.FixedIncome.VasicekBondPrice")
    assert path_to_module("MathFin.BlackScholes.Call") == "MathFin.BlackScholes.Call"
    assert path_to_module("MathFin/Foo.lean") == "MathFin.Foo"


def _write_index(d):
    with open(os.path.join(d, "types.jsonl"), "w") as f:
        f.write(json.dumps({"name": "MathFin.vasicekBondPrice", "module":
                            "MathFin.FixedIncome.VasicekBondPrice", "type": "ℝ → ℝ → ℝ",
                            "docString": "Vasicek ZCB price.", "allowCompletion": True}) + "\n")
        f.write(json.dumps({"name": "MathFin.other", "module":
                            "MathFin.FixedIncome.VasicekBondPrice", "type": "ℝ → ℝ",
                            "docString": None, "allowCompletion": True}) + "\n")
        f.write(json.dumps({"name": "MathFin.elsewhere", "module":
                            "MathFin.BlackScholes.Call", "type": "ℝ", "docString": None,
                            "allowCompletion": True}) + "\n")
    with open(os.path.join(d, "tactics.jsonl"), "w") as f:
        f.write(json.dumps({"module": "MathFin.FixedIncome.VasicekBondPrice",
                            "goals": [{"pp": "⊢ 0 < x", "usedConstants": []}],
                            "goalsAfter": [], "ppTac": "positivity", "kind": "k"}) + "\n")
        f.write(json.dumps({"module": "MathFin.BlackScholes.Call",
                            "goals": [{"pp": "⊢ a = b"}], "ppTac": "ring", "kind": "k"}) + "\n")
    with open(os.path.join(d, "const_dep.jsonl"), "w") as f:
        f.write(json.dumps({"name": "MathFin.vasicekBondPrice", "module":
                            "MathFin.FixedIncome.VasicekBondPrice",
                            "deps": ["Real.exp", "MathFin.affineA"], "allowCompletion": True}) + "\n")


def test_available_false_when_absent():
    with tempfile.TemporaryDirectory() as d:
        assert ScoutIndex(d).available is False
        assert ScoutIndex(None).available is False


def test_signatures_filters_by_module_and_carries_type_and_doc():
    with tempfile.TemporaryDirectory() as d:
        _write_index(d)
        idx = ScoutIndex(d)
        assert idx.available
        sigs = idx.signatures(["MathFin/FixedIncome/VasicekBondPrice.lean"])
        assert set(sigs) == {"MathFin.FixedIncome.VasicekBondPrice"}
        recs = sigs["MathFin.FixedIncome.VasicekBondPrice"]
        assert ("MathFin.vasicekBondPrice", "ℝ → ℝ → ℝ", "Vasicek ZCB price.") in recs
        # a decl from a non-requested module is excluded
        assert all("elsewhere" not in n for n, _, _ in recs)


def test_signatures_respects_max_per_module():
    with tempfile.TemporaryDirectory() as d:
        _write_index(d)
        idx = ScoutIndex(d)
        sigs = idx.signatures(["MathFin.FixedIncome.VasicekBondPrice"], max_per_module=1)
        assert len(sigs["MathFin.FixedIncome.VasicekBondPrice"]) == 1


def test_tactic_exemplars_returns_goal_tactic_pairs():
    with tempfile.TemporaryDirectory() as d:
        _write_index(d)
        idx = ScoutIndex(d)
        ex = idx.tactic_exemplars(["MathFin.FixedIncome.VasicekBondPrice"])
        assert ("⊢ 0 < x", "positivity") in ex
        # other module not requested → excluded
        assert all(tac != "ring" for _, tac in ex)


def test_dependencies_lookup():
    with tempfile.TemporaryDirectory() as d:
        _write_index(d)
        idx = ScoutIndex(d)
        assert idx.dependencies("MathFin.vasicekBondPrice") == ["Real.exp", "MathFin.affineA"]
        assert idx.dependencies("MathFin.nope") == []


def test_default_index_dir_points_at_foundry_index():
    assert default_index_dir("/x/foundry").replace("\\", "/") == "/x/foundry/index"
