"""Pure tests for vibe_prove — injected run_fn, no vibe/docker/Lean."""
from __future__ import annotations

import os

import vibe_prove


import domain_pack

PACK = domain_pack.load("mathfin")


def test_sanitize_stem_makes_a_safe_module_name():
    assert vibe_prove.sanitize_stem("cal-bk-67") == "_Autoform_cal_bk_67"
    assert vibe_prove.sanitize_stem("mf/thm.9.1") == "_Autoform_mf_thm_9_1"


def test_scratch_paths_host_and_container_align():
    host, rel = vibe_prove.scratch_paths(PACK, "/home/x/main", "cal-bk-67")
    assert host == "/home/x/main/MathFin/_Autoform_cal_bk_67.lean"
    assert rel == "MathFin/_Autoform_cal_bk_67.lean"
    # the relative path is what vibe uses (CWD=main) AND what the MCP sees under /app
    assert host.endswith(rel)


def test_build_vibe_task_names_the_file_and_the_theorem():
    t = vibe_prove.build_vibe_task("MathFin/_Autoform_x.lean", "myThm", "")
    assert "MathFin/_Autoform_x.lean" in t
    assert "theorem myThm" in t
    assert "Do NOT change the theorem statement" in t
    assert "EXISTING RESULTS" not in t  # no pointer pack → no consume block


def test_build_vibe_task_includes_context_pack_when_present():
    t = vibe_prove.build_vibe_task("f.lean", "t", "vasicekBondPrice_affine : …")
    assert "EXISTING RESULTS TO CONSUME" in t
    assert "vasicekBondPrice_affine" in t


def test_run_vibe_target_captures_the_edited_file_and_cleans_up(tmp_path):
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-x-1", "sorry_name": "foo",
              "statement": "import Mathlib\ntheorem foo : True := by sorry\n"}
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), target["id"])

    def fake_vibe(argv, cwd=None, check=None):
        # vibe runs with CWD = main repo, and edits the in-place host file
        assert cwd == str(tmp_path)
        assert argv[0] == "/x/leanstral-vibe.sh" and "-p" in argv
        assert os.path.exists(host)  # the stub was materialized before vibe ran
        with open(host, "w", encoding="utf-8") as f:
            f.write("import Mathlib\ntheorem foo : True := trivial\n")
        return 0

    cand = vibe_prove.run_vibe_target(PACK, 
        target, main_repo=str(tmp_path), context_pack="", max_turns=10,
        vibe_script="/x/leanstral-vibe.sh", run_fn=fake_vibe)
    assert cand is not None and "trivial" in cand and "sorry" not in cand
    assert not os.path.exists(host)  # scratch always cleaned up


def test_run_vibe_target_returns_stub_on_a_no_op_and_still_cleans_up(tmp_path):
    (tmp_path / "MathFin").mkdir()
    target = {"id": "cal-x-2", "sorry_name": "bar",
              "statement": "import Mathlib\ntheorem bar : True := by sorry\n"}
    host, _ = vibe_prove.scratch_paths(PACK, str(tmp_path), target["id"])

    cand = vibe_prove.run_vibe_target(PACK, 
        target, main_repo=str(tmp_path), context_pack="", max_turns=10,
        vibe_script="/x/leanstral-vibe.sh", run_fn=lambda *a, **k: 0)
    assert cand is not None and "sorry" in cand  # unchanged stub captured
    assert not os.path.exists(host)
