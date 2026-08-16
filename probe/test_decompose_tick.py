"""Tests for the decompose-tick driver's file wiring (Task 2.8). Injected chat/check
fns + a fake runs dir — no API, no daemon, no docker."""

from __future__ import annotations

import json

from decompose import assemble_skeleton, dag_to_dict, parse_dag
from decompose_tick import do_draft, do_recompose

import domain_pack

PACK = domain_pack.load("mathfin")


_DAG = {
    "main": {"name": "main_thm", "statement": "theorem main_thm (x : ℝ) : x = x",
             "proof": "by exact lem_refl x"},
    "leaves": [{"name": "lem_refl", "statement": "theorem lem_refl (x : ℝ) : x = x",
                "pointers": ["MathFin/Foundations/X.lean"], "depends_on": []}],
}


def _proved_module(name):
    return ("/-\nCopyright\n-/\nmodule\n\npublic import Mathlib\n\n@[expose] public section\n\n"
            f"namespace MathFin\n\ntheorem {name} (x : ℝ) : x = x := rfl\n\nend MathFin\n")


def test_do_draft_writes_dag_and_leaf_manifest_on_skeleton_pass(tmp_path):
    runs = str(tmp_path)
    good_chat = lambda msgs, **_kw: (json.dumps(_DAG), 7)              # noqa: E731
    clean = lambda code: {"errors": [], "sorry_count": code.count("sorry")}  # noqa: E731
    r = do_draft(PACK, "cal-bk-99", "T", runs, target_text="theorem main_thm (x : ℝ) : x = x",
                 context_pack="", drafter_preamble="", cfg_max_leaves=3, cfg_max_reask=1,
                 chat_fn=good_chat, check_fn=clean)
    assert r["outcome"] == "drafted" and r["leaves_total"] == 1 and r["tokens"] == 7
    dag = parse_dag(json.load(open(tmp_path / "T-cal-bk-99.dag.json")))
    assert dag.main.name == "main_thm"
    man = json.load(open(tmp_path / "T-cal-bk-99-leaves" / "manifest.json"))
    assert man["targets"][0]["sorry_name"] == "lem_refl"
    assert man["targets"][0]["id"] == "cal-bk-99__lem_refl"


def test_do_draft_reports_skeleton_failure(tmp_path):
    good_chat = lambda msgs, **_kw: (json.dumps(_DAG), 1)             # noqa: E731
    boom = lambda code: {"errors": ["type mismatch"], "sorry_count": 9}  # noqa: E731
    r = do_draft(PACK, "cal-bk-99", "T", str(tmp_path), target_text="t", context_pack="",
                 drafter_preamble="", cfg_max_leaves=3, cfg_max_reask=1,
                 chat_fn=good_chat, check_fn=boom)
    assert r["outcome"] == "fail_skeleton" and not (tmp_path / "T-cal-bk-99.dag.json").exists()


def test_do_recompose_assembles_candidate_from_proved_leaves(tmp_path):
    runs = str(tmp_path)
    # lay down what do_draft would have written
    (tmp_path / "T-cal-bk-99.dag.json").write_text(json.dumps(_DAG), encoding="utf-8")
    leafdir = tmp_path / "T-cal-bk-99-leaves"
    leafdir.mkdir()
    (leafdir / "manifest.json").write_text(json.dumps(
        {"targets": [{"id": "cal-bk-99__lem_refl", "sorry_name": "lem_refl"}]}), encoding="utf-8")
    # the proved leaf module vibe would have written
    (tmp_path / "T-cal-bk-99__lem_refl.lean").write_text(_proved_module("lem_refl"), encoding="utf-8")

    r = do_recompose(PACK, "cal-bk-99", "T", runs, check_fn=lambda m: {"passed": True, "reason": "ok"})
    assert r["outcome"] == "pass" and r["leaves_closed"] == 1 and r["leaves_total"] == 1
    cand = (tmp_path / "T-cal-bk-99.lean").read_text(encoding="utf-8")
    assert "theorem lem_refl (x : ℝ) : x = x := rfl" in cand
    assert "theorem main_thm (x : ℝ) : x = x := by exact lem_refl x" in cand
    assert "sorry" not in cand


def test_do_recompose_partial_banks_and_declares_remainder(tmp_path):
    (tmp_path / "T-cal-bk-99.dag.json").write_text(json.dumps(_DAG), encoding="utf-8")
    leafdir = tmp_path / "T-cal-bk-99-leaves"
    leafdir.mkdir()
    (leafdir / "manifest.json").write_text(json.dumps(
        {"targets": [{"id": "cal-bk-99__lem_refl", "sorry_name": "lem_refl"}]}), encoding="utf-8")
    # no proved leaf on disk → partial; the gate must not even be called
    called = {"n": 0}
    r = do_recompose(PACK, "cal-bk-99", "T", str(tmp_path),
                     check_fn=lambda m: called.__setitem__("n", called["n"] + 1) or {"passed": True})
    assert r["outcome"] == "partial" and r["remainder"] == ["lem_refl"] and r["leaves_closed"] == 0
    assert called["n"] == 0 and not (tmp_path / "T-cal-bk-99.lean").exists()


def test_draft_then_recompose_chains(tmp_path):
    # the real path-contract between the two shell steps: do_draft's leaf-manifest ids +
    # the vibe-written `<tag>-<leafid>.lean` convention must be exactly what do_recompose reads.
    runs = str(tmp_path)
    good_chat = lambda msgs, **_kw: (json.dumps(_DAG), 1)                    # noqa: E731
    clean = lambda code: {"errors": [], "sorry_count": code.count("sorry")}  # noqa: E731
    d = do_draft(PACK, "cal-bk-99", "T", runs, target_text="t", context_pack="", drafter_preamble="",
                 cfg_max_leaves=3, cfg_max_reask=1, chat_fn=good_chat, check_fn=clean)
    assert d["outcome"] == "drafted"
    # vibe would write the proved leaf module at this exact path (from the manifest id)
    leaf_id = d["leaf_ids"][0]
    (tmp_path / f"T-{leaf_id}.lean").write_text(_proved_module("lem_refl"), encoding="utf-8")
    r = do_recompose(PACK, "cal-bk-99", "T", runs, check_fn=lambda m: {"passed": True})
    assert r["outcome"] == "pass" and r["leaves_closed"] == 1
    assert (tmp_path / "T-cal-bk-99.lean").exists()


def test_skeleton_smoke():
    # guard the driver's contract with assemble_skeleton stays intact
    assert "by sorry" in assemble_skeleton(PACK, parse_dag(dag_to_dict(parse_dag(_DAG))))
