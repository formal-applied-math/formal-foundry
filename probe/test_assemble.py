import json
import os
import tempfile

from assemble import append_entry, apply_contribution


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
        written = apply_contribution("theorem foo : True := trivial\n",
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
        apply_contribution("theorem foo : True := trivial", target, {"id": "id"}, main)
        assert open(os.path.join(main, "MathFin/A.lean")).read().endswith("\n")
