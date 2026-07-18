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
