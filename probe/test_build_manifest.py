"""Tests for build_manifest helpers (pure; the daemon-gated build is not tested here)."""

from build_manifest import parse_meta, parse_pointers


def test_parse_pointers_reads_comment_line():
    code = (
        "import MathFin.BlackScholes.Call\n"
        "-- pointers: MathFin/BlackScholes/Call.lean, MathFin/BlackScholes/Forward.lean\n"
        "theorem t : True := by sorry\n"
    )
    assert parse_pointers(code) == [
        "MathFin/BlackScholes/Call.lean",
        "MathFin/BlackScholes/Forward.lean",
    ]


def test_parse_pointers_empty_when_absent():
    assert parse_pointers("theorem t : True := by sorry\n") == []


def test_parse_pointers_trims_and_drops_blanks():
    assert parse_pointers("--  pointers:  A.lean ,, B.lean ,\n") == ["A.lean", "B.lean"]


def test_parse_meta_reads_placement_header():
    code = (
        "-- pointers: MathFin/BlackScholes/Forward.lean\n"
        "-- main-module: MathFin/FX/InterestRateParity.lean\n"
        "-- benchmark: benchmarks/mathematical_finance.json\n"
        "-- benchmark-id: mf-fx-interest-rate-parity\n"
        "-- source-issue: 108\n"
        "theorem t : True := by sorry\n"
    )
    assert parse_meta(code) == {
        "main_module": "MathFin/FX/InterestRateParity.lean",
        "benchmark": "benchmarks/mathematical_finance.json",
        "benchmark_id": "mf-fx-interest-rate-parity",
        "source_issue": 108,
    }


def test_parse_meta_empty_when_absent():
    assert parse_meta("theorem t : True := by sorry\n") == {}


def test_parse_meta_source_issue_tolerates_hash():
    assert parse_meta("-- source-issue: #109\n")["source_issue"] == 109


def test_parse_meta_reads_deferred_subset_header():
    # a SUBSET proof rides its remaining facts on a `-- deferred:` header (`;`-joined);
    # parse_meta splits them back to a list so open-pr can surface them as follow-ups.
    code = (
        "-- main-module: MathFin/Futures/Contango.lean\n"
        "-- source-issue: 88\n"
        "-- deferred: term-structure monotonicity: T ↦ F(T) increasing iff r > δ; "
        "basis → 0 as T→0⁺\n"
        "theorem t : True := by sorry\n"
    )
    m = parse_meta(code)
    assert m["source_issue"] == 88
    assert m["deferred"] == [
        "term-structure monotonicity: T ↦ F(T) increasing iff r > δ",
        "basis → 0 as T→0⁺",
    ]


def test_parse_meta_no_deferred_key_when_full_issue():
    # the common case (full-issue proof) carries no `-- deferred:` header at all.
    assert "deferred" not in parse_meta("-- source-issue: 88\ntheorem t : True := by sorry\n")


def test_load_entry_reads_sidecar():
    import json, os, tempfile
    from build_manifest import load_entry
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "cal-bk-88.lean")
        open(stub, "w").close()
        with open(os.path.join(d, "cal-bk-88.entry.json"), "w") as f:
            json.dump({"id": "mf-fx-contango", "metadata":
                       {"provenance": {"source": "leanstral-autoform", "issue": 88}}}, f)
        entry = load_entry(stub)
        assert entry["id"] == "mf-fx-contango"
        assert entry["metadata"]["provenance"]["source"] == "leanstral-autoform"


def test_load_entry_none_when_absent():
    import os, tempfile
    from build_manifest import load_entry
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "cal-bk-99.lean")
        open(stub, "w").close()
        assert load_entry(stub) is None
