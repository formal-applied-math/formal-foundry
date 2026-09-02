"""Tests for the lemma-DAG schema + validation (Task 2.1). Pure logic — no Lean,
no API, no network."""

from __future__ import annotations

import pytest

from decompose import MAX_LEAVES, DagError, parse_dag, topo_order

import domain_pack

PACK = domain_pack.load("mathfin")


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
    r = draft_decomposition(PACK, "prove P", "", chat_fn=good)
    assert r["ok"] and r["dag"].main.name == "main" and r["tokens"] == 5

    calls = {"n": 0}

    def bad(msgs, **_kw):
        calls["n"] += 1
        return ("here is my answer: not a dag", 3)
    r2 = draft_decomposition(PACK, "prove P", "", chat_fn=bad, max_reask=1)
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
    draft_decomposition(PACK, "prove P", "", chat_fn=cap, feedback="skeleton did not elaborate: boom")
    assert "boom" in seen["user"]


def test_draft_decomposition_extracts_json_with_lean_braces():
    # a Lean `{x : ℝ}` implicit binder inside a leaf statement must not break brace
    # matching in the JSON extractor.
    reply = ('```json\n{"main": {"name": "m", "statement": "∀ x, P x"}, '
             '"leaves": [{"name": "a", "statement": "theorem a {x : ℝ} : P x := by sorry", '
             '"pointers": [], "depends_on": []}]}\n```')
    from decompose import draft_decomposition
    r = draft_decomposition(PACK, "t", "", chat_fn=lambda msgs, **_kw: (reply, 1))
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
    lean = assemble_skeleton(PACK, dag)
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
    man = build_leaf_manifest(PACK, dag, {"id": "cal-bk-99", "main_module": "MathFin/M.lean"},
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
    man = build_leaf_manifest(PACK, dag, {"id": "cal-bk-99"}, str(tmp_path), proved=proved)
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
    assert extract_leaf_decl(PACK, _PROVED_LEM, "lem_refl") == "theorem lem_refl (x : ℝ) : x = x := rfl"
    assert extract_leaf_decl(PACK, _PROVED_LEM, "nope") is None


def test_recompose_full_and_partial():
    from decompose import recompose
    dag = parse_dag(_SKEL_FIXTURE)

    # all leaves proved + the assembled module passes the full gate → ok
    full = recompose(PACK, dag, {"lem_refl": _PROVED_LEM}, check_fn=lambda m: {"passed": True})
    assert full["ok"] and not full["partial"] and full["banked"] == ["lem_refl"]
    assert "theorem lem_refl (x : ℝ) : x = x := rfl" in full["module"]
    assert "theorem main_thm (x : ℝ) : x = x := by exact lem_refl x" in full["module"]
    assert "sorry" not in full["module"]

    # all proved but the RECOMPOSITION fails the full gate → not ok, not partial
    rej = recompose(PACK, dag, {"lem_refl": _PROVED_LEM},
                    check_fn=lambda m: {"passed": False, "reason": "recompose mismatch"})
    assert not rej["ok"] and not rej["partial"] and rej["reason"] == "recompose mismatch"
    assert "module" in rej

    # partial: a leaf unproved → banked + declared remainder (deferred, refs not closes),
    # never a silent gap; no module assembled and the gate is not even called.
    called = {"n": 0}
    part = recompose(PACK, dag, {}, check_fn=lambda m: called.__setitem__("n", called["n"] + 1) or {"passed": True})
    assert not part["ok"] and part["partial"] and part["deferred"]
    assert part["banked"] == [] and part["remainder"] == ["lem_refl"]
    assert called["n"] == 0


# --- structural-split playbook: cases / induction / suffices ------------------
# The lemma-DAG's `main.proof` is arbitrary Lean, so a split whose difficulty lives
# INSIDE one proof (a case split, an induction, a goal reduction) needs no schema
# change — the main dispatches to sorried leaves via `rcases` / `induction ... with` /
# `suffices`, and the existing skeleton gate validates it. Confirmed against the real
# elaborator 2026-07-29 (errors:[], sorry_count == n_leaves for all three shapes).
# These tests pin that the decomposer is TAUGHT the moves and the machinery assembles
# them.

def test_decompose_system_teaches_structural_split_patterns():
    s = PACK.prompt("decompose-system")
    low = s.lower()
    # case-split: dispatch to one leaf per branch
    assert "rcases" in s or "by_cases" in s
    # induction: base + step leaves, the IH as the step's hypothesis
    assert "induction" in low
    assert "induction hypothesis" in low or "ih" in low
    # goal reduction
    assert "suffices" in low


_CASES_DAG = {
    "main": {"name": "bar", "statement": "theorem bar (x : ℤ) : 0 ≤ x * x",
             "proof": "by\n  rcases le_total 0 x with h | h\n  · exact bar_nonneg x h\n  · exact bar_neg x h"},
    "leaves": [
        {"name": "bar_nonneg", "statement": "theorem bar_nonneg (x : ℤ) (h : 0 ≤ x) : 0 ≤ x * x"},
        {"name": "bar_neg", "statement": "theorem bar_neg (x : ℤ) (h : x ≤ 0) : 0 ≤ x * x"},
    ],
}

_INDUCTION_DAG = {
    "main": {"name": "foo", "statement": "theorem foo (n : ℕ) : n + 0 = n",
             "proof": "by\n  induction n with\n  | zero => exact foo_base\n  | succ k ih => exact foo_step k ih"},
    "leaves": [
        {"name": "foo_base", "statement": "theorem foo_base : 0 + 0 = 0"},
        {"name": "foo_step",
         "statement": "theorem foo_step (k : ℕ) (ih : k + 0 = k) : (k + 1) + 0 = k + 1"},
    ],
}


def test_cases_dag_assembles_and_gates():
    from decompose import assemble_skeleton, skeleton_gate
    dag = parse_dag(_CASES_DAG)
    lean = assemble_skeleton(PACK, dag)
    # the case-dispatch tactic survives verbatim into the main theorem's proof
    assert "rcases le_total 0 x with h | h" in lean
    # each branch is an ordinary single-sorry leaf carrying its branch hypothesis
    assert "theorem bar_nonneg (x : ℤ) (h : 0 ≤ x) : 0 ≤ x * x := by sorry" in lean
    assert "theorem bar_neg (x : ℤ) (h : x ≤ 0) : 0 ≤ x * x := by sorry" in lean
    # clean elaboration with one sorry per branch leaf → the gate accepts the split
    g = skeleton_gate(lean, len(dag.leaves),
                      check_fn=lambda c: {"errors": [], "sorry_count": 2})
    assert g["passed"]


def test_induction_dag_assembles_and_gates():
    from decompose import assemble_skeleton, skeleton_gate
    dag = parse_dag(_INDUCTION_DAG)
    lean = assemble_skeleton(PACK, dag)
    # `induction ... with` dispatches zero/succ to the base and step leaves
    assert "induction n with" in lean
    assert "| succ k ih => exact foo_step k ih" in lean
    # the step leaf carries the induction hypothesis as an explicit premise
    assert ("theorem foo_step (k : ℕ) (ih : k + 0 = k) : (k + 1) + 0 = k + 1 := by sorry"
            in lean)
    g = skeleton_gate(lean, len(dag.leaves),
                      check_fn=lambda c: {"errors": [], "sorry_count": 2})
    assert g["passed"]


# --- hardening: orphan-leaf rejection + applied_to proving hints --------------

def test_dag_rejects_orphan_leaf():
    # a leaf the main proof never dispatches to (and no reachable leaf depends on) is
    # dead weight — reject so it never burns prover budget. Checked only when the main
    # carries a real proof.
    orphan = {"main": {"name": "m", "statement": "theorem m : P", "proof": "by exact used"},
              "leaves": [{"name": "used", "statement": "theorem used : P"},
                         {"name": "dead", "statement": "theorem dead : Q"}]}
    with pytest.raises(DagError):
        parse_dag(orphan)


def test_dag_orphan_check_follows_depends_on_and_skips_sketch():
    # `helper` is pulled in transitively via `used.depends_on` → NOT an orphan
    dag = parse_dag({"main": {"name": "m", "statement": "theorem m : P", "proof": "by exact used h"},
                     "leaves": [{"name": "used", "statement": "theorem used : P",
                                 "depends_on": ["helper"]},
                                {"name": "helper", "statement": "theorem helper : R"}]})
    assert {leaf.name for leaf in dag.leaves} == {"used", "helper"}
    # an empty/sketch main proof (schema-validation shape) → reachability not assessed
    dag2 = parse_dag({"main": {"name": "m", "statement": "P"},
                      "leaves": [{"name": "a", "statement": "x"}]})
    assert dag2.leaves[0].name == "a"


def test_applied_to_roundtrips_and_surfaces_as_prove_hint(tmp_path):
    from decompose import build_leaf_manifest, dag_to_dict
    spec = {"main": {"name": "m", "statement": "theorem m (x : ℤ) : 0 ≤ x * x",
                     "proof": "by exact leaf1 x"},
            "leaves": [{"name": "leaf1", "statement": "theorem leaf1 (x : ℤ) : 0 ≤ x * x",
                        "pointers": ["MathFin/A.lean"],
                        "applied_to": ["mul_self_nonneg", "le_total"]}]}
    dag = parse_dag(spec)
    assert dag.leaves[0].applied_to == ["mul_self_nonneg", "le_total"]
    # round-trips through the persisted DAG shape
    again = parse_dag(dag_to_dict(dag))
    assert again.leaves[0].applied_to == ["mul_self_nonneg", "le_total"]
    # the per-leaf stub carries the hint as a comment the vibe prover reads,
    # while staying an ordinary single-sorry target
    man = build_leaf_manifest(PACK, dag, {"id": "t1"}, str(tmp_path))
    stub = (tmp_path / man["targets"][0]["file"]).read_text(encoding="utf-8")
    assert "-- apply: mul_self_nonneg, le_total" in stub
    assert stub.count("sorry") == 1


def test_applied_to_absent_leaves_no_hint_comment(tmp_path):
    from decompose import build_leaf_manifest
    dag = parse_dag(_INDUCTION_DAG)   # its leaves carry no applied_to
    man = build_leaf_manifest(PACK, dag, {"id": "t2"}, str(tmp_path))
    for t in man["targets"]:
        stub = (tmp_path / t["file"]).read_text(encoding="utf-8")
        assert "-- apply:" not in stub


def test_applied_to_must_be_list_of_strings():
    with pytest.raises(DagError):
        parse_dag({"main": {"name": "m", "statement": "theorem m : P", "proof": "by exact a"},
                   "leaves": [{"name": "a", "statement": "theorem a : P",
                               "applied_to": "not-a-list"}]})
