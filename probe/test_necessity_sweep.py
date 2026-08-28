"""Daemon-free tests for the necessity sweep driver."""
import necessity_sweep as ns

WRAPPER = '''import MathFin.Performance.RatiosExtended

open MathFin

theorem mf_performance_gain_to_pain {ι : Type*} (s : Finset ι) (r : ι → ℝ) :
    0 ≤ gainToPain s r :=
  MathFin.gainToPain_nonneg s r
'''

GUARDED = '''import MathFin.Performance.RatiosExtended

theorem gainToPain_nonneg_of_denom_pos {S : Type*} (finset_S : Finset S) (r : S → ℝ)
    (h : 0 < ∑ s ∈ finset_S, max (-r s) 0) : 0 ≤ gainToPain S finset_S r := by
  positivity
'''


def test_primary_decl_reads_the_last_declaration():
    assert ns.primary_decl(WRAPPER) == "mf_performance_gain_to_pain"
    assert ns.primary_decl(GUARDED) == "gainToPain_nonneg_of_denom_pos"


def test_primary_decl_is_none_when_there_is_no_theorem():
    assert ns.primary_decl("def f : Nat := 3\n") is None


def test_data_binders_are_pre_filtered_because_dropping_them_cannot_elaborate():
    # `s` and `r` both occur in the conclusion `0 ≤ gainToPain s r`, so dropping
    # either is a certain elaboration failure and must not cost a daemon call.
    assert ns.probe_worthy_binders(WRAPPER, "mf_performance_gain_to_pain") == []


def test_a_hypothesis_binder_survives_the_pre_filter():
    # `h` appears nowhere in the remaining signature or the conclusion.
    assert ns.probe_worthy_binders(GUARDED, "gainToPain_nonneg_of_denom_pos") == ["h"]


def test_pre_filter_is_name_boundary_aware():
    code = '''theorem t (h : True) (hs : Nat) : hs = hs := by rfl\n'''
    # `h` is not used by `hs` — a substring match would wrongly pre-filter it.
    assert "h" in ns.probe_worthy_binders(code, "t")


def test_loader_partitions_by_provenance_and_status(tmp_path):
    bench = tmp_path / "d.json"
    bench.write_text(__import__("json").dumps({
        "description": "d",
        "theorems": [
            {"id": "a", "code": {"lean": GUARDED},
             "metadata": {"formalization_status": "full"}},
            {"id": "b", "code": {"lean": WRAPPER},
             "metadata": {"formalization_status": "library_wrapper",
                          "provenance": {"source": "leanstral-autoform"}}},
        ]}), encoding="utf-8")
    entries = ns.load_mathfin_entries(str(tmp_path / "*.json"))
    assert [(e.entry_id, e.status, e.provenance) for e in entries] == [
        ("a", "full", "human"), ("b", "library_wrapper", "leanstral-autoform")]
    assert entries[0].domain == "d"


def test_power_control_true_when_the_sweep_closes_the_original():
    calls = []

    def fake_prove(probe):
        calls.append(probe)
        return {"lean_text": probe.replace("sorry", "positivity"), "tokens": 0}

    assert ns.sweep_can_prove(GUARDED, "gainToPain_nonneg_of_denom_pos",
                              prove_fn=fake_prove) is True
    # the control probes the ORIGINAL signature — no binder was dropped
    assert "(h : 0 < " in calls[0]
    assert "sorry" in calls[0]


def test_power_control_false_when_the_sweep_returns_the_probe_untouched():
    def fake_prove(probe):
        return {"lean_text": probe, "tokens": 0}    # unchanged == not closed

    assert ns.sweep_can_prove(GUARDED, "gainToPain_nonneg_of_denom_pos",
                              prove_fn=fake_prove) is False


def test_power_control_false_when_the_declaration_cannot_be_located():
    def fake_prove(probe):
        raise AssertionError("must not be called")

    assert ns.sweep_can_prove(GUARDED, "no_such_theorem", prove_fn=fake_prove) is False


#: every explicit binder of GUARDED, so a fake prover can tell which one a probe dropped
GUARDED_BINDERS = ("finset_S", "r", "h")


def _fakes(closes: set[str]):
    """check_fn accepts everything; prove_fn closes the untouched original — so the
    power control passes, as it must for a theorem whose real proof is `positivity`,
    the sweep's own first tactic — and closes a reduced probe exactly when every
    binder it dropped is named in `closes`."""
    def check_fn(code):
        return {"errors": [], "sorry_count": 1 if "sorry" in code else 0}

    def prove_fn(probe):
        dropped = {nm for nm in GUARDED_BINDERS if f"({nm} :" not in probe}
        closed = True if not dropped else dropped <= closes
        return {"lean_text": probe.replace("sorry", "positivity") if closed else probe,
                "tokens": 0}
    return check_fn, prove_fn


def test_a_removable_hypothesis_is_certified_unnecessary():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "certified_unnecessary"
    assert binder["closing_tactic"] == "positivity"
    assert binder["sweep_proves_original"] is True


def test_every_entry_emits_exactly_one_power_control_record():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert sum(1 for r in recs if r["verdict"] == "power_control") == 1


def test_a_red_regate_is_not_a_positive():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": False, "reason": "axioms"})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "not_shown_unnecessary"


def test_daemon_trouble_records_an_error_and_never_a_verdict():
    def check_fn(code):
        return {"error": "connection refused", "errors": ["connection refused"]}

    def prove_fn(probe):
        return {"lean_text": probe, "tokens": 0}

    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    verdicts = {r["verdict"] for r in recs if r["binder"] is not None}
    assert verdicts <= {"daemon_error"}
    assert "certified_unnecessary" not in verdicts


def test_done_keys_reads_back_what_was_written(tmp_path):
    p = tmp_path / "out.jsonl"
    import json as _j
    p.write_text("\n".join(_j.dumps({"arm": "mathfin", "entry_id": x})
                           for x in ("a", "b")) + "\n", encoding="utf-8")
    assert ns.done_keys(str(p)) == {("mathfin", "a"), ("mathfin", "b")}


def test_done_keys_is_empty_when_the_file_does_not_exist(tmp_path):
    assert ns.done_keys(str(tmp_path / "nope.jsonl")) == set()


def test_done_keys_tolerates_a_truncated_final_line(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text('{"arm": "mathfin", "entry_id": "a"}\n{"arm": "mathfin", "ent',
                 encoding="utf-8")
    assert ns.done_keys(str(p)) == {("mathfin", "a")}


def test_run_sweep_skips_entries_already_recorded(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    out = str(tmp_path / "out.jsonl")
    first = ns.run_sweep([e], out, check_fn=check_fn, prove_fn=prove_fn,
                         regate_fn=lambda c: {"passed": True}, log=lambda m: None)
    second = ns.run_sweep([e], out, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True}, log=lambda m: None)
    assert first["entries"] == 1 and first["skipped"] == 0
    assert second["entries"] == 0 and second["skipped"] == 1


MODULE_SRC = '''import Mathlib

namespace MathFin

/-- doc -/
noncomputable def gainToPain {ι : Type*} (s : Finset ι) (r : ι → ℝ) : ℝ :=
  (∑ i ∈ s, posPart (r i)) / (∑ i ∈ s, negPart (r i))

abbrev painIndex (x : ℝ) : ℝ := -x

theorem gainToPain_nonneg {ι : Type*} (s : Finset ι) (r : ι → ℝ) :
    0 ≤ gainToPain s r := by positivity

end MathFin
'''


def _mathfin_root(tmp_path):
    d = tmp_path / "MathFin" / "Performance"
    d.mkdir(parents=True)
    (d / "RatiosExtended.lean").write_text(MODULE_SRC, encoding="utf-8")
    return str(tmp_path)


def test_module_defs_reads_the_imported_modules_own_definitions(tmp_path):
    # WRAPPER imports MathFin.Performance.RatiosExtended and names `gainToPain`.
    assert ns.module_defs(WRAPPER, _mathfin_root(tmp_path)) == ["gainToPain"]


def test_module_defs_drops_definitions_the_statement_never_names(tmp_path):
    # `painIndex` is defined in the same module but absent from the statement;
    # splicing it into `unfold` would just make every sweep tactic fail to elaborate.
    assert "painIndex" not in ns.module_defs(WRAPPER, _mathfin_root(tmp_path))


def test_module_defs_ignores_mathlib_imports_and_missing_modules(tmp_path):
    code = "import Mathlib\nimport MathFin.Nope\n\ntheorem t (n : Nat) : n + 0 = n := by simp\n"
    assert ns.module_defs(code, _mathfin_root(tmp_path)) == []


def test_run_sweep_builds_a_prover_per_entry_when_given_a_factory(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    seen = []

    def prove_for(entry):
        seen.append(entry.entry_id)
        return prove_fn

    e = ns.Entry("mathfin", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    ns.run_sweep([e], str(tmp_path / "out.jsonl"), check_fn=check_fn,
                 prove_for=prove_for, regate_fn=lambda c: {"passed": True},
                 log=lambda m: None)
    assert seen == ["e1"]


def test_run_sweep_refuses_both_a_prover_and_a_factory(tmp_path):
    import pytest
    check_fn, prove_fn = _fakes({"h"})
    with pytest.raises(TypeError):
        ns.run_sweep([], str(tmp_path / "out.jsonl"), check_fn=check_fn,
                     prove_fn=prove_fn, prove_for=lambda e: prove_fn,
                     regate_fn=lambda c: {"passed": True}, log=lambda m: None)


def test_run_metadata_pins_the_corpus_the_sweep_actually_read(tmp_path):
    import json as _j
    out = str(tmp_path / "out.jsonl")
    ns.write_run_meta(out, arm="mathfin", corpus_root=str(tmp_path), extra={"status": "full"})
    lines = open(out + ".meta.jsonl", encoding="utf-8").read().strip().split("\n")
    meta = _j.loads(lines[-1])
    assert meta["arm"] == "mathfin" and meta["status"] == "full"
    assert "corpus_commit" in meta and "started_utc" in meta
    assert meta["sweep_tactics"][0] == "positivity"


def test_run_metadata_appends_one_line_per_resume(tmp_path):
    out = str(tmp_path / "out.jsonl")
    ns.write_run_meta(out, arm="mathfin", corpus_root=str(tmp_path))
    ns.write_run_meta(out, arm="mathfin", corpus_root=str(tmp_path))
    assert len(open(out + ".meta.jsonl", encoding="utf-8").read().strip().split("\n")) == 2


def test_run_metadata_records_an_unknown_commit_rather_than_failing(tmp_path):
    import json as _j
    out = str(tmp_path / "out.jsonl")
    # tmp_path is not a git checkout, so there is no commit to read
    ns.write_run_meta(out, arm="mathfin", corpus_root=str(tmp_path))
    meta = _j.loads(open(out + ".meta.jsonl", encoding="utf-8").read().strip())
    assert meta["corpus_commit"] == "unknown"
