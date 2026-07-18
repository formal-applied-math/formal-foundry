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


def test_dag_to_dict_roundtrips():
    from decompose import dag_to_dict
    dag = parse_dag(_FIXTURE)
    again = parse_dag(dag_to_dict(dag))
    assert [n.name for n in topo_order(again)] == ["lemA", "lemB", "main"]
    assert again.main.proof == dag.main.proof


def test_draft_decomposition_seeds_feedback_into_first_message():
    # the skeleton-gate re-decompose passes the elaboration errors as `feedback`; it must
    # reach the very first decomposer message (not only after a DagError).
    import json
    from decompose import draft_decomposition
    seen = {}

    def cap(msgs, **_kw):
        seen["user"] = msgs[-1]["content"]
        return (json.dumps(_FIXTURE), 1)
    draft_decomposition("prove P", "", chat_fn=cap, feedback="skeleton did not elaborate: boom")
    assert "boom" in seen["user"]


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


# --- Task 2.5: recompose + keep-and-revise -----------------------------------

_PROVED_LEM = (
    "/-\nCopyright\n-/\nmodule\n\npublic import Mathlib\n\n"
    "@[expose] public section\n\nnamespace MathFin\n\n"
    "theorem lem_refl (x : ℝ) : x = x := rfl\n\nend MathFin\n"
)


def test_extract_leaf_decl():
    from decompose import extract_leaf_decl
    assert extract_leaf_decl(_PROVED_LEM, "lem_refl") == "theorem lem_refl (x : ℝ) : x = x := rfl"
    assert extract_leaf_decl(_PROVED_LEM, "nope") is None


def test_recompose_full_and_partial():
    from decompose import recompose
    dag = parse_dag(_SKEL_FIXTURE)

    # all leaves proved + the assembled module passes the full gate → ok
    full = recompose(dag, {"lem_refl": _PROVED_LEM}, check_fn=lambda m: {"passed": True})
    assert full["ok"] and not full["partial"] and full["banked"] == ["lem_refl"]
    assert "theorem lem_refl (x : ℝ) : x = x := rfl" in full["module"]
    assert "theorem main_thm (x : ℝ) : x = x := by exact lem_refl x" in full["module"]
    assert "sorry" not in full["module"]

    # all proved but the RECOMPOSITION fails the full gate → not ok, not partial
    rej = recompose(dag, {"lem_refl": _PROVED_LEM},
                    check_fn=lambda m: {"passed": False, "reason": "recompose mismatch"})
    assert not rej["ok"] and not rej["partial"] and rej["reason"] == "recompose mismatch"
    assert "module" in rej

    # partial: a leaf unproved → banked + declared remainder (deferred, refs not closes),
    # never a silent gap; no module assembled and the gate is not even called.
    called = {"n": 0}
    part = recompose(dag, {}, check_fn=lambda m: called.__setitem__("n", called["n"] + 1) or {"passed": True})
    assert not part["ok"] and part["partial"] and part["deferred"]
    assert part["banked"] == [] and part["remainder"] == ["lem_refl"]
    assert called["n"] == 0
