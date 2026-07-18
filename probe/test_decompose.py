"""Tests for the lemma-DAG schema + validation (Task 2.1). Pure logic — no Lean,
no API, no network."""

from __future__ import annotations

import pytest

from decompose import MAX_LEAVES, DagError, parse_dag, topo_order

_FIXTURE = {
    "main": {"name": "main", "statement": "P", "proof_sketch": "apply lemA then lemB"},
    "leaves": [
        {"name": "lemB", "statement": "Q", "pointers": ["MathFin/X.lean"], "depends_on": ["lemA"]},
        {"name": "lemA", "statement": "R", "pointers": [], "depends_on": []},
    ],
}


def test_dag_parse_and_validate():
    dag = parse_dag(_FIXTURE)
    assert [n.name for n in topo_order(dag)] == ["lemA", "lemB", "main"]
    assert dag.main.is_main and dag.main.name == "main"
    assert len(dag.leaves) == 2


def test_dag_rejects_cycles_and_oversize():
    cyclic = {"main": {"name": "m", "statement": "P"},
              "leaves": [{"name": "a", "statement": "x", "depends_on": ["b"]},
                         {"name": "b", "statement": "y", "depends_on": ["a"]}]}
    with pytest.raises(DagError):
        parse_dag(cyclic)
    oversize = {"main": {"name": "m", "statement": "P"},
                "leaves": [{"name": f"l{i}", "statement": "x"} for i in range(MAX_LEAVES + 1)]}
    with pytest.raises(DagError):
        parse_dag(oversize)


def test_dag_rejects_dangling_dep_bad_shape_and_nonjson():
    with pytest.raises(DagError):   # depends_on an unknown leaf
        parse_dag({"main": {"name": "m", "statement": "P"},
                   "leaves": [{"name": "a", "statement": "x", "depends_on": ["ghost"]}]})
    with pytest.raises(DagError):   # no main, no leaves
        parse_dag({"leaves": []})
    with pytest.raises(DagError):   # not JSON
        parse_dag("not json{")
    with pytest.raises(DagError):   # main name collides with a leaf
        parse_dag({"main": {"name": "a", "statement": "P"},
                   "leaves": [{"name": "a", "statement": "x"}]})


def test_dag_accepts_json_string():
    import json
    dag = parse_dag(json.dumps(_FIXTURE))
    assert dag.main.name == "main"


# --- Task 2.2: the decomposer call -------------------------------------------

def test_draft_decomposition_returns_valid_dag_or_error():
    import json
    from decompose import draft_decomposition

    good = lambda msgs, **_kw: (json.dumps(_FIXTURE), 5)   # noqa: E731
    r = draft_decomposition("prove P", "", chat_fn=good)
    assert r["ok"] and r["dag"].main.name == "main" and r["tokens"] == 5

    calls = {"n": 0}

    def bad(msgs, **_kw):
        calls["n"] += 1
        return ("here is my answer: not a dag", 3)
    r2 = draft_decomposition("prove P", "", chat_fn=bad, max_reask=1)
    assert not r2["ok"] and r2["error"]
    assert calls["n"] == 2   # initial + exactly one re-ask, then stop (no infinite loop)


def test_draft_decomposition_extracts_json_with_lean_braces():
    # a Lean `{x : ℝ}` implicit binder inside a leaf statement must not break brace
    # matching in the JSON extractor.
    reply = ('```json\n{"main": {"name": "m", "statement": "∀ x, P x"}, '
             '"leaves": [{"name": "a", "statement": "theorem a {x : ℝ} : P x := by sorry", '
             '"pointers": [], "depends_on": []}]}\n```')
    from decompose import draft_decomposition
    r = draft_decomposition("t", "", chat_fn=lambda msgs, **_kw: (reply, 1))
    assert r["ok"] and r["dag"].leaves[0].name == "a"


# --- Task 2.3: the skeleton-elaboration gate ---------------------------------

_SKEL_FIXTURE = {
    "main": {"name": "main_thm", "statement": "theorem main_thm (x : ℝ) : x = x",
             "proof": "by exact lem_refl x"},
    "leaves": [{"name": "lem_refl", "statement": "theorem lem_refl (x : ℝ) : x = x",
                "pointers": ["MathFin/Foundations/X.lean"], "depends_on": []}],
}


def test_skeleton_gate_requires_full_elaboration_with_n_sorries():
    from decompose import assemble_skeleton, skeleton_gate
    dag = parse_dag(_SKEL_FIXTURE)
    n = len(dag.leaves)
    lean = assemble_skeleton(dag)
    assert "theorem lem_refl (x : ℝ) : x = x := by sorry" in lean
    assert "theorem main_thm (x : ℝ) : x = x := by exact lem_refl x" in lean
    assert "public import MathFin.Foundations.X" in lean

    # clean elaboration with exactly n_leaves sorries → pass
    ok = skeleton_gate(lean, n, check_fn=lambda c: {"success": True, "errors": [], "sorry_count": n})
    assert ok["passed"] and not ok["indeterminate"]
    # the main also left sorried (too many sorries) → fail
    extra = skeleton_gate(lean, n, check_fn=lambda c: {"errors": [], "sorry_count": n + 1})
    assert not extra["passed"] and "sorries" in extra["verdict"]
    # the main's proof does not elaborate → fail
    err = skeleton_gate(lean, n, check_fn=lambda c: {"errors": ["type mismatch"], "sorry_count": n})
    assert not err["passed"]
    # daemon infra error → indeterminate (Task 1.4 sentinel), never a false pass
    ind = skeleton_gate(lean, n, check_fn=lambda c: {"error": "daemon timeout"})
    assert ind["indeterminate"] and not ind["passed"]


# --- Task 2.4: leaf routing through the existing prove+gate path --------------

_DAG_FIXTURE = {
    "main": {"name": "main_thm", "statement": "theorem main_thm (x : ℝ) : x = x",
             "proof": "by exact lem_two x"},
    "leaves": [
        {"name": "lem_two", "statement": "theorem lem_two (x : ℝ) : x = x",
         "pointers": ["MathFin/B.lean"], "depends_on": ["lem_one"]},
        {"name": "lem_one", "statement": "theorem lem_one (y : ℝ) : y = y",
         "pointers": ["MathFin/A.lean"], "depends_on": []},
    ],
}


def test_manifest_accepts_dag_leaf_targets(tmp_path):
    from decompose import build_leaf_manifest
    dag = parse_dag(_DAG_FIXTURE)
    man = build_leaf_manifest(dag, {"id": "cal-bk-99", "main_module": "MathFin/M.lean"},
                              str(tmp_path), toolchain="leanprover/lean4:v4.31.0",
                              main_commit="deadbeef")
    # one target per leaf, in topo order (dependency first), each linked to the parent
    assert [t["sorry_name"] for t in man["targets"]] == ["lem_one", "lem_two"]
    assert [t["dag_order"] for t in man["targets"]] == [0, 1]
    for t in man["targets"]:
        assert t["parent"] == "main_thm" and t["kind"] == "prove"
        assert t["parent_id"] == "cal-bk-99"
        # the per-leaf stub is an ordinary single-sorry target vibe_prove reads verbatim
        stub = (tmp_path / t["file"]).read_text(encoding="utf-8")
        assert stub.count("sorry") == 1
        assert f"theorem {t['sorry_name']}" in stub and ":= by sorry" in stub
        assert t["input_hash"]
    # the manifest is the shape vibe_prove._iter_targets consumes
    assert man["toolchain"] == "leanprover/lean4:v4.31.0" and man["main_commit"] == "deadbeef"
    assert (tmp_path / "manifest.json").exists()


def test_leaf_stub_inlines_proved_dependency(tmp_path):
    # keep-and-revise: a proved dependency is inlined ABOVE a dependent leaf so its
    # proof can consume it, and the stub stays single-sorry (the dep carries no sorry).
    from decompose import build_leaf_manifest
    dag = parse_dag(_DAG_FIXTURE)
    proved = {"lem_one": "theorem lem_one (y : ℝ) : y = y := rfl"}
    man = build_leaf_manifest(dag, {"id": "cal-bk-99"}, str(tmp_path), proved=proved)
    two = next(t for t in man["targets"] if t["sorry_name"] == "lem_two")
    stub = (tmp_path / two["file"]).read_text(encoding="utf-8")
    assert "theorem lem_one (y : ℝ) : y = y := rfl" in stub
    assert stub.count("sorry") == 1     # only lem_two's own sorry
