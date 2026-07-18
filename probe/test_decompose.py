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
