"""Tests for the lean_scout neighborhood slice (index_filter).

The property under test is the one the old `grep '"module":"MathFin'` step got
wrong: a Mathlib lemma our library actually consumes must SURVIVE into the
index, while the ~99% of the transitive closure we never touch must not."""

import json
import os
import tempfile

import index_filter as ixf


def _write(d, name, recs):
    path = os.path.join(d, name)
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return path


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# --- module_matches: prefix-with-dot, not bare startswith ---------------------

def test_module_matches_exact_and_submodule():
    assert ixf.module_matches("MathFin", ("MathFin",))
    assert ixf.module_matches("MathFin.BlackScholes.Call", ("MathFin",))


def test_module_matches_rejects_prefix_lookalikes():
    # the bug a bare startswith would have: a different library whose name
    # happens to begin with ours must not be treated as own/allowed.
    assert not ixf.module_matches("MathFinance.Foo", ("MathFin",))
    assert not ixf.module_matches("Mathlibrary.X", ("Mathlib",))
    assert not ixf.module_matches(None, ("MathFin",))


# --- the frontier -------------------------------------------------------------

def test_reached_constants_only_from_own_modules():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "const_dep.jsonl", [
            {"name": "MathFin.a", "module": "MathFin.Foo",
             "deps": ["MeasureTheory.Integrable.add", "Real.exp"]},
            # a Mathlib decl's own deps are NOT our frontier — including them
            # would transitively pull in most of Mathlib.
            {"name": "MeasureTheory.Integrable.add", "module": "Mathlib.MeasureTheory.Integral",
             "deps": ["Nat.succ", "Set.univ"]},
        ])
        assert ixf.reached_constants(p) == {"MeasureTheory.Integrable.add", "Real.exp"}


def test_neighborhood_is_the_modules_hosting_reached_constants():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "types.jsonl", [
            {"name": "MeasureTheory.Integrable.add", "module": "Mathlib.MeasureTheory.Integral"},
            {"name": "MeasureTheory.Integrable.sub", "module": "Mathlib.MeasureTheory.Integral"},
            {"name": "Polynomial.roots", "module": "Mathlib.Algebra.Polynomial"},
        ])
        mods = ixf.neighborhood_modules(p, {"MeasureTheory.Integrable.add"})
        assert mods == {"Mathlib.MeasureTheory.Integral"}


def test_neighborhood_excludes_disallowed_namespaces():
    # `Eq.mpr` / `Nat.succ` are reached by essentially every proof; hosting
    # modules like Init.Prelude must stay out or the slice is meaningless.
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "types.jsonl", [
            {"name": "Eq.mpr", "module": "Init.Core"},
            {"name": "Real.exp", "module": "Mathlib.Analysis.SpecialFunctions.Exp"},
        ])
        mods = ixf.neighborhood_modules(p, {"Eq.mpr", "Real.exp"})
        assert mods == {"Mathlib.Analysis.SpecialFunctions.Exp"}


def test_neighborhood_excludes_own_modules():
    with tempfile.TemporaryDirectory() as d:
        p = _write(d, "types.jsonl", [{"name": "MathFin.helper", "module": "MathFin.Foo"}])
        assert ixf.neighborhood_modules(p, {"MathFin.helper"}) == set()


# --- the slice ----------------------------------------------------------------

def _full_index(d):
    _write(d, "const_dep.jsonl", [
        {"name": "MathFin.price", "module": "MathFin.BlackScholes.Call",
         "deps": ["MeasureTheory.Integrable.add", "Eq.mpr"]},
        {"name": "MeasureTheory.Integrable.add", "module": "Mathlib.MeasureTheory.Integral",
         "deps": []},
        {"name": "Polynomial.roots", "module": "Mathlib.Algebra.Polynomial", "deps": []},
        {"name": "Eq.mpr", "module": "Init.Core", "deps": []},
    ])
    _write(d, "types.jsonl", [
        {"name": "MathFin.price", "module": "MathFin.BlackScholes.Call", "type": "ℝ"},
        {"name": "MeasureTheory.Integrable.add", "module": "Mathlib.MeasureTheory.Integral",
         "type": "…"},
        # the sibling: never referenced by us, but in a module we live in, so it
        # SHOULD survive — this is the whole point of a module-granular slice.
        {"name": "MeasureTheory.Integrable.sub", "module": "Mathlib.MeasureTheory.Integral",
         "type": "…"},
        {"name": "Polynomial.roots", "module": "Mathlib.Algebra.Polynomial", "type": "…"},
        {"name": "Eq.mpr", "module": "Init.Core", "type": "…"},
    ])
    _write(d, "tactics.jsonl", [
        {"module": "MathFin.BlackScholes.Call", "ppTac": "positivity"},
        {"module": "Mathlib.MeasureTheory.Integral", "ppTac": "simp"},
    ])


def test_slice_keeps_own_and_the_reached_neighborhood():
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        stats = ixf.slice_index(d)
        names = {r["name"] for r in _read(os.path.join(d, "types.jsonl"))}
        assert "MathFin.price" in names                      # own
        assert "MeasureTheory.Integrable.add" in names       # reached
        assert "MeasureTheory.Integrable.sub" in names       # module-mate of a reached decl
        assert "Polynomial.roots" not in names               # untouched Mathlib
        assert "Eq.mpr" not in names                         # disallowed namespace
        assert stats["types"] == {"kept": 3, "total": 5}
        assert stats["neighborhood_modules"] == 1


def test_slice_keeps_tactics_own_only():
    # tactic exemplars teach HOUSE style; Mathlib's own tactics are not that.
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        ixf.slice_index(d)
        mods = {r["module"] for r in _read(os.path.join(d, "tactics.jsonl"))}
        assert mods == {"MathFin.BlackScholes.Call"}


def test_slice_also_prunes_const_dep():
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        ixf.slice_index(d)
        names = {r["name"] for r in _read(os.path.join(d, "const_dep.jsonl"))}
        assert names == {"MathFin.price", "MeasureTheory.Integrable.add"}


def test_slice_is_idempotent():
    # re-running after a partial failure must not shrink the index further: the
    # frontier is recomputed from the surviving own-module records, which are
    # exactly the ones the first pass kept.
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        first = ixf.slice_index(d)
        after_first = _read(os.path.join(d, "types.jsonl"))
        second = ixf.slice_index(d)
        assert _read(os.path.join(d, "types.jsonl")) == after_first
        assert second["types"]["kept"] == first["types"]["kept"]


def test_missing_tactics_file_is_tolerated():
    # tactics extraction is opt-in (SCOUT_TACTICS=1); its absence is normal.
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        os.remove(os.path.join(d, "tactics.jsonl"))
        stats = ixf.slice_index(d)
        assert stats["tactics"] == {"kept": 0, "total": 0}


def test_unparseable_line_is_skipped_not_fatal():
    # an hour-long extraction must not be lost to one bad line.
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        with open(os.path.join(d, "types.jsonl"), "a", encoding="utf-8") as f:
            f.write("{not json\n")
        stats = ixf.slice_index(d)
        assert stats["types"]["kept"] == 3


def test_filter_leaves_original_intact_on_missing_input():
    with tempfile.TemporaryDirectory() as d:
        assert ixf.filter_file(os.path.join(d, "nope.jsonl"), set()) == (0, 0)


def test_cli_reports_and_exits_zero(capsys):
    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        assert ixf.main([d]) == 0
        err = capsys.readouterr().err
        assert "frontier" in err and "types" in err


# --- the seam that actually broke ---------------------------------------------
# Every unit on both sides of this passed while the pipeline as a whole was
# wrong: the build script kept MathFin-only records and `embed.load_premises`
# faithfully embedded exactly what it was given, so the drafter's semantic
# retrieval could not surface a Mathlib lemma. The contract worth pinning is the
# composition, not either half.

def test_sliced_index_feeds_both_consumers_with_mathlib_visible():
    import embed
    from scout_index import ScoutIndex

    with tempfile.TemporaryDirectory() as d:
        _full_index(d)
        ixf.slice_index(d)

        # consumer 1: the premise corpus the drafter searches semantically
        names = {p["name"] for p in embed.load_premises(d)}
        assert "MeasureTheory.Integrable.add" in names, \
            "a Mathlib lemma MathFin consumes must be retrievable"
        assert "MeasureTheory.Integrable.sub" in names, \
            "so must its module-mate — that is the anti-wrapper payload"
        assert "Polynomial.roots" not in names
        assert embed.corpus_composition(embed.load_premises(d))["Mathlib"] == 2

        # consumer 2: the context-pack adapter
        idx = ScoutIndex(d)
        assert idx.available
        sig = idx.signature_of("MeasureTheory.Integrable.add")
        assert sig is not None and sig[0] == "Mathlib.MeasureTheory.Integral"
        # own signatures still resolve by module, as house_context asks for them
        assert "MathFin.BlackScholes.Call" in idx.signatures(["MathFin/BlackScholes/Call.lean"])


def test_own_only_index_still_works_when_nothing_external_is_reached():
    # a library with no external deps recorded must degrade to the old behaviour
    # rather than producing an empty index.
    import embed
    with tempfile.TemporaryDirectory() as d:
        _write(d, "const_dep.jsonl", [{"name": "MathFin.a", "module": "MathFin.Foo", "deps": []}])
        _write(d, "types.jsonl", [{"name": "MathFin.a", "module": "MathFin.Foo",
                                   "type": "ℝ", "allowCompletion": True}])
        stats = ixf.slice_index(d)
        assert stats["neighborhood_modules"] == 0
        assert [p["name"] for p in embed.load_premises(d)] == ["MathFin.a"]
