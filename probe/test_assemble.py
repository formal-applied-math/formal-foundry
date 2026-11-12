import json
import os
import tempfile

from assemble import append_entry, apply_contribution


import domain_pack

PACK = domain_pack.load("mathfin")


def test_append_entry_preserves_formatting_and_trailing_newline():
    original = ('{\n  "description": "d",\n  "theorems": [\n'
                '    {\n      "id": "a"\n    }\n  ]\n}\n')  # trailing newline
    out = append_entry(original, {"id": "b", "name": "B"})
    data = json.loads(out)
    assert [t["id"] for t in data["theorems"]] == ["a", "b"]
    assert out.endswith("}\n")                        # newline preserved
    assert '    {\n      "id": "a"\n    },' in out    # existing entry byte-preserved (now comma'd)


def test_append_entry_no_trailing_newline_preserved():
    original = '{\n  "theorems": [\n    {\n      "id": "a"\n    }\n  ]\n}'  # NO trailing newline
    out = append_entry(original, {"id": "b"})
    assert not out.endswith("\n")


def test_append_entry_bare_list_file():
    out = append_entry('[\n  {\n    "id": "a"\n  }\n]\n', {"id": "b"})
    assert [t["id"] for t in json.loads(out)] == ["a", "b"]


def test_apply_contribution_writes_module_and_entry():
    with tempfile.TemporaryDirectory() as main:
        os.makedirs(os.path.join(main, "benchmarks"))
        with open(os.path.join(main, "benchmarks", "x.json"), "w") as f:
            f.write('{\n  "theorems": [\n    {\n      "id": "old"\n    }\n  ]\n}\n')
        target = {"main_module": "MathFin/FX/IRP.lean",
                  "benchmark": "benchmarks/x.json", "benchmark_id": "mf-irp"}
        entry = {"id": "mf-irp", "name": "IRP", "code": {"lean": "..."}}
        written = apply_contribution(PACK, "theorem foo : True := trivial\n",
                                     target, entry, main)
        assert "MathFin/FX/IRP.lean" in written
        assert "benchmarks/x.json" in written
        assert open(os.path.join(main, "MathFin/FX/IRP.lean")).read().startswith("theorem foo")
        data = json.load(open(os.path.join(main, "benchmarks", "x.json")))
        assert [t["id"] for t in data["theorems"]] == ["old", "mf-irp"]


def test_apply_contribution_adds_trailing_newline_to_module():
    with tempfile.TemporaryDirectory() as main:
        os.makedirs(os.path.join(main, "benchmarks"))
        with open(os.path.join(main, "benchmarks", "x.json"), "w") as f:
            f.write('{\n  "theorems": []\n}\n')
        target = {"main_module": "MathFin/A.lean",
                  "benchmark": "benchmarks/x.json", "benchmark_id": "id"}
        apply_contribution(PACK, "theorem foo : True := trivial", target, {"id": "id"}, main)
        assert open(os.path.join(main, "MathFin/A.lean")).read().endswith("\n")


def test_apply_contribution_refuses_to_overwrite_existing_module():
    # A new-object contribution creates a NEW module; the candidate is a whole
    # standalone file (license + `module` + `namespace`), so writing it over an
    # EXISTING module deletes that module's theorems. #162 hit this: the drafter set
    # main_module to MathFin/Performance/RatiosExtended.lean (an existing module it
    # also imported), apply_contribution clobbered it, and AxiomAuditGen then failed
    # with "unknown constant" for the deleted theorems. Refuse loud-and-early here so
    # no route can silently corrupt an existing module.
    with tempfile.TemporaryDirectory() as main:
        os.makedirs(os.path.join(main, "benchmarks"))
        with open(os.path.join(main, "benchmarks", "x.json"), "w") as f:
            f.write('{\n  "theorems": []\n}\n')
        os.makedirs(os.path.join(main, "MathFin", "Performance"))
        existing = os.path.join(main, "MathFin", "Performance", "RatiosExtended.lean")
        with open(existing, "w") as f:
            f.write("theorem informationRatio_scale_invariant : True := trivial\n")
        target = {"main_module": "MathFin/Performance/RatiosExtended.lean",
                  "benchmark": "benchmarks/x.json", "benchmark_id": "id"}
        try:
            apply_contribution(PACK, "theorem upCapture_x : True := trivial\n",
                               target, {"id": "id"}, main)
            raised = False
        except FileExistsError:
            raised = True
        assert raised, "must refuse to overwrite an existing module"
        assert "informationRatio_scale_invariant" in open(existing).read()  # untouched


def test_ensure_umbrella_import_appends_once():
    from assemble import ensure_umbrella_import
    with tempfile.TemporaryDirectory() as main:
        with open(os.path.join(main, "MathFin.lean"), "w") as f:
            f.write("import MathFin.A\nimport MathFin.B\n")
        changed = ensure_umbrella_import(main, "MathFin/FX/Siegel.lean", PACK.namespace + ".lean")
        assert changed == ["MathFin.lean"]
        text = open(os.path.join(main, "MathFin.lean")).read()
        assert "import MathFin.FX.Siegel\n" in text
        assert ensure_umbrella_import(main, "MathFin/FX/Siegel.lean", PACK.namespace + ".lean") == []  # idempotent


def test_ensure_umbrella_import_handles_no_trailing_newline():
    from assemble import ensure_umbrella_import
    with tempfile.TemporaryDirectory() as main:
        with open(os.path.join(main, "MathFin.lean"), "w") as f:
            f.write("import MathFin.A")  # no trailing newline
        ensure_umbrella_import(main, "MathFin/C.lean", PACK.namespace + ".lean")
        text = open(os.path.join(main, "MathFin.lean")).read()
        assert text == "import MathFin.A\nimport MathFin.C\n"


# --- the prover is stamped from the session that ran, not assumed ----------------

def test_stamp_prover_names_the_model_that_proved_the_candidate():
    from assemble import stamp_prover
    from assemble import PIPELINE_SOURCE
    # as the emitter seeds it: nothing is proved yet, so no prover is named
    seed = {"metadata": {
        "formalization_scope": "Full formal proof in M.lean (autoformalized statement, "
                               "machine proof). Re-export from MathFin. Axioms-clean.",
        "provenance": {"source": PIPELINE_SOURCE, "issue": 129}}}
    out, changed = stamp_prover(seed, model="claude-sonnet-5-5")
    prov = out["metadata"]["provenance"]
    assert prov["model"] == "claude-sonnet-5-5"
    assert prov["source"] == PIPELINE_SOURCE          # the pipeline's counting marker
    assert ("(autoformalized statement, claude-sonnet-5-5 proof)"
            in out["metadata"]["formalization_scope"])
    assert set(changed) == {"model", "formalization_scope"}
    assert "model" not in seed["metadata"]["provenance"]   # copied, not mutated
    # stamping the same prover again changes nothing
    same, changed2 = stamp_prover(out, model="claude-sonnet-5-5")
    assert changed2 == [] and same == out


def test_stamp_prover_overwrites_a_seed_from_before_the_prover_changed():
    """A stub staged while another prover was configured names it in the seed. The entry
    that lands must name the session that ran, in all three places."""
    from assemble import PIPELINE_SOURCE, stamp_prover
    stale = {"metadata": {
        "formalization_scope": "Full formal proof in M.lean (autoformalized statement, "
                               "leanstral proof).",
        "provenance": {"source": "leanstral-autoform", "model": "labs-leanstral-1-5",
                       "issue": 129}}}
    out, changed = stamp_prover(stale, model="claude-sonnet-5-5")
    assert out["metadata"]["provenance"] == {
        "source": PIPELINE_SOURCE, "model": "claude-sonnet-5-5", "issue": 129}
    assert "leanstral" not in json.dumps(out).lower()
    assert set(changed) == {"model", "source", "formalization_scope"}
