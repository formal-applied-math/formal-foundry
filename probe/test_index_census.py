"""A slice that keeps nothing must say what it saw instead.

`types 0 / 766629 (0.00%)` is true and useless: it cost a 30-minute extraction to
learn only that the econometrics index came out empty, with no way to distinguish
"the library was never in the environment" from "its modules are named something
else" from "the records carry no module field at all".

The census answers that in the same run. These tests pin the three diagnoses apart,
because a diagnostic that cannot tell them apart is decoration.
"""
from __future__ import annotations

import json
import os
import tempfile

import index_filter as ixf


def _write(d: str, name: str, recs: list[dict]) -> str:
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return p


def test_census_names_the_namespaces_actually_present():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "types.jsonl",
               [{"name": "Mathlib.foo", "module": "Mathlib.Algebra.Group", "type": "X"}] * 5
               + [{"name": "Init.bar", "module": "Init.Prelude", "type": "X"}] * 2)
        census = dict(ixf.namespace_census(os.path.join(d, "types.jsonl")))
        assert census == {"Mathlib": 5, "Init": 2}


def test_census_distinguishes_a_missing_module_field():
    """Different diagnosis, different fix: absent namespace vs malformed records."""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "types.jsonl",
               [{"name": "a", "type": "X"}, {"name": "b", "module": None, "type": "X"},
                {"name": "c", "module": "Mathlib.X", "type": "X"}])
        census = dict(ixf.namespace_census(os.path.join(d, "types.jsonl")))
        assert census["<no module field>"] == 2
        assert census["Mathlib"] == 1


def test_slice_reports_a_census_when_it_keeps_nothing():
    """The live case: a real corpus with no own-namespace declaration in it."""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "types.jsonl",
               [{"name": f"Mathlib.f{i}", "module": "Mathlib.Analysis.Basic", "type": "X"}
                for i in range(20)])
        _write(d, "const_dep.jsonl", [])
        stats = ixf.slice_index(d, own=("Econometrics",))
        assert stats["types"] == {"kept": 0, "total": 20}
        assert "census" in stats, "a 0% slice must explain itself"
        assert dict(stats["census"]) == {"Mathlib": 20}


def test_no_census_when_the_slice_kept_something():
    """Silence when there is nothing to diagnose — the census is a failure report,
    not routine noise on every successful build."""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "types.jsonl",
               [{"name": "Econometrics.condMean", "module": "Econometrics.Identification",
                 "type": "X"}])
        _write(d, "const_dep.jsonl", [])
        stats = ixf.slice_index(d, own=("Econometrics",))
        assert stats["types"]["kept"] == 1
        assert "census" not in stats


def test_no_census_on_a_genuinely_empty_input():
    """0/0 is not the same finding as 0/766629 and must not masquerade as one."""
    with tempfile.TemporaryDirectory() as d:
        _write(d, "types.jsonl", [])
        _write(d, "const_dep.jsonl", [])
        stats = ixf.slice_index(d, own=("Econometrics",))
        assert stats["types"] == {"kept": 0, "total": 0}
        assert "census" not in stats
