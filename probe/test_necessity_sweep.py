"""Daemon-free tests for the necessity sweep driver."""
import domain_pack
import necessity_sweep as ns

PACK = domain_pack.load("mathfin")

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
    entries = ns.load_catalogue_entries(str(tmp_path / "*.json"))
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
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "certified_unnecessary"
    assert binder["closing_tactic"] == "positivity"
    assert binder["sweep_proves_original"] is True


def test_every_entry_emits_exactly_one_power_control_record():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert sum(1 for r in recs if r["verdict"] == "power_control") == 1


def test_a_red_regate_is_not_a_positive():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": False, "reason": "axioms"})
    binder = [r for r in recs if r["binder"] == "h"][0]
    assert binder["verdict"] == "not_shown_unnecessary"


def test_daemon_trouble_records_an_error_and_never_a_verdict():
    """The entry has to be REACHABLE for a binder probe to run at all now that a blind
    entry short-circuits, so the prover closes the untouched original and the daemon
    fails only on the reduced probe — which is the path this test is about."""
    def check_fn(code):
        if "(h :" in code:            # nothing dropped: the power control's own probe
            return {"errors": [], "sorry_count": 1}
        return {"error": "connection refused", "errors": ["connection refused"]}

    def prove_fn(probe):
        return {"lean_text": probe.replace("sorry", "positivity"), "tokens": 0}

    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    verdicts = {r["verdict"] for r in recs if r["binder"] is not None}
    assert verdicts <= {"daemon_error"}
    assert "certified_unnecessary" not in verdicts


def test_done_keys_reads_back_what_was_written(tmp_path):
    p = tmp_path / "out.jsonl"
    import json as _j
    p.write_text("\n".join(_j.dumps({"arm": "catalogue", "entry_id": x})
                           for x in ("a", "b")) + "\n", encoding="utf-8")
    assert ns.done_keys(str(p)) == {("catalogue", "a"), ("catalogue", "b")}


def test_done_keys_is_empty_when_the_file_does_not_exist(tmp_path):
    assert ns.done_keys(str(tmp_path / "nope.jsonl")) == set()


def test_done_keys_tolerates_a_truncated_final_line(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text('{"arm": "catalogue", "entry_id": "a"}\n{"arm": "catalogue", "ent',
                 encoding="utf-8")
    assert ns.done_keys(str(p)) == {("catalogue", "a")}


def test_run_sweep_skips_entries_already_recorded(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
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
    assert ns.module_defs(PACK, WRAPPER, _mathfin_root(tmp_path)) == ["gainToPain"]


def test_module_defs_drops_definitions_the_statement_never_names(tmp_path):
    # `painIndex` is defined in the same module but absent from the statement;
    # splicing it into `unfold` would just make every sweep tactic fail to elaborate.
    assert "painIndex" not in ns.module_defs(PACK, WRAPPER, _mathfin_root(tmp_path))


def test_module_defs_ignores_mathlib_imports_and_missing_modules(tmp_path):
    code = "import Mathlib\nimport MathFin.Nope\n\ntheorem t (n : Nat) : n + 0 = n := by simp\n"
    assert ns.module_defs(PACK, code, _mathfin_root(tmp_path)) == []


def test_run_sweep_builds_a_prover_per_entry_when_given_a_factory(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    seen = []

    def prove_for(entry):
        seen.append(entry.entry_id)
        return prove_fn

    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
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


def _corpus(counts):
    """One entry per (domain, index), each carrying `counts[domain]` worthy binders is
    not expressible with real Lean; instead give every entry the single binder `h` of
    GUARDED and vary how many entries each domain has."""
    out = []
    for domain, n in counts.items():
        for i in range(n):
            out.append(ns.Entry("catalogue", f"{domain}-{i}", domain,
                                "gainToPain_nonneg_of_denom_pos", "full", "human",
                                GUARDED))
    return out


def test_sample_is_deterministic_under_a_seed():
    E = _corpus({"a": 30, "b": 20})
    first = ns.stratified_binder_sample(E, 10, seed=20260913)
    again = ns.stratified_binder_sample(E, 10, seed=20260913)
    assert [(e.entry_id, b) for e, b in first] == [(e.entry_id, b) for e, b in again]


def test_sample_draws_exactly_the_requested_number_of_binders():
    E = _corpus({"a": 30, "b": 20})
    got = ns.stratified_binder_sample(E, 17, seed=20260913)
    assert sum(len(b) for _e, b in got) == 17


def test_sample_allocates_across_domains_in_proportion():
    E = _corpus({"a": 40, "b": 10})          # 40 binders vs 10, so 4:1
    got = ns.stratified_binder_sample(E, 20, seed=20260913)
    per = {}
    for e, b in got:
        per[e.domain] = per.get(e.domain, 0) + len(b)
    assert per == {"a": 16, "b": 4}


def test_sample_larger_than_the_population_returns_everything():
    E = _corpus({"a": 3})
    got = ns.stratified_binder_sample(E, 999, seed=1)
    assert sum(len(b) for _e, b in got) == 3


def test_sweep_entry_probes_only_the_binders_it_was_given():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True}, binders=[])
    assert [r["verdict"] for r in recs] == ["power_control"]


def test_run_sweep_restricts_to_the_sampled_binders(tmp_path):
    import json as _j
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    out = str(tmp_path / "out.jsonl")
    ns.run_sweep([e], out, check_fn=check_fn, prove_fn=prove_fn,
                 regate_fn=lambda c: {"passed": True}, binders_for={"e1": []},
                 log=lambda m: None)
    recs = [_j.loads(l) for l in open(out, encoding="utf-8")]
    assert [r["verdict"] for r in recs] == ["power_control"]


def test_drawing_then_limiting_is_not_the_same_as_limiting_then_drawing():
    """A pilot must be the real run's first N entries, or its cost projects nothing.

    `main` applies `--limit` after the draw for this reason: truncating first samples
    out of a truncated corpus, which is a different population reached by a different
    allocation, so its per-record cost says nothing about the run it is meant to size.
    """
    E = _corpus({"a": 12, "b": 8})

    draw_then_limit = [e.entry_id for e, _b in
                       ns.stratified_binder_sample(E, 10, seed=20260913)[:4]]
    limit_then_draw = [e.entry_id for e, _b in
                       ns.stratified_binder_sample(E[:4], 10, seed=20260913)]

    # limiting first cannot reach past the first four entries, whatever the draw says
    assert limit_then_draw == ["a-0", "a-1", "a-2", "a-3"]
    assert draw_then_limit != limit_then_draw


def _blind_fakes():
    """A prover that closes nothing — so the power control fails and the entry is blind."""
    def check_fn(code):
        return {"errors": [], "sorry_count": 1 if "sorry" in code else 0}

    def prove_fn(probe):
        return {"lean_text": probe, "tokens": 0}
    return check_fn, prove_fn


def test_a_blind_entrys_binders_are_recorded_but_never_probed():
    check_fn, prove_fn = _blind_fakes()
    probed = []

    def counting_check(code):
        probed.append(code)
        return check_fn(code)

    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=counting_check, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    binder = [r for r in recs if r["binder"] == "h"][0]
    # the record exists, so the population is still fully accounted for ...
    assert binder["verdict"] == "power_control_failed"
    assert binder["sweep_proves_original"] is False
    # ... but nothing expensive was spent on it: no probe carrying the theorem's
    # imports was sent. The one call made is the import-free liveness probe, which is
    # what keeps a dead daemon from being recorded as blindness.
    assert not [c for c in probed if "import" in c]
    assert probed == [ns._LIVENESS_PROBE]
    assert binder["elapsed_s"] == 0.0


def test_the_short_circuit_can_be_turned_off_for_a_census_of_attempts():
    check_fn, prove_fn = _blind_fakes()
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True}, skip_blind=False)
    assert [r["verdict"] for r in recs if r["binder"]] == ["not_shown_unnecessary"]


def test_a_reachable_entry_still_probes_every_binder():
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert [r["verdict"] for r in recs if r["binder"]] == ["certified_unnecessary"]


PROBE = "import MathFin\n\ntheorem t (h : True) : 0 ≤ 1 := by sorry\n"


def test_batched_prover_spends_one_call_when_nothing_closes():
    calls = []

    def check_fn(code):
        calls.append(code)
        return {"errors": ["unsolved goals"], "sorry_count": 0}

    prove = ns.batched_sweep_prover(check_fn, ("gainToPain",))
    got = prove(PROBE)
    assert len(calls) == 1                       # not eight
    assert got["lean_text"] == PROBE             # unchanged == not closed


def test_batched_prover_guards_every_alternative_with_done():
    """Without `done` a tactic that succeeds while leaving goals open would commit
    `first` to itself, and the sweep would report a failure where the per-tactic
    version would have moved on."""
    calls = []

    def check_fn(code):
        calls.append(code)
        return {"errors": ["nope"], "sorry_count": 0}

    ns.batched_sweep_prover(check_fn, ("gainToPain",))(PROBE)
    sent = calls[0]
    assert "first" in sent
    assert sent.count("; done)") >= 6            # one per live tactic slot
    assert "sorry" not in sent


def test_batched_prover_hands_back_a_single_tactic_proof_not_the_first_chain():
    """A positive must carry the tactic that actually did the work — the paper reports
    which sweep slot closed it, and a `first | ...` chain in the proof is not that."""
    seen = []

    def check_fn(code):
        seen.append(code)
        if "first" in code:
            return {"errors": [], "sorry_count": 0}       # the batched probe closes
        # the identifying pass: only `grind` works
        ok = code.rstrip().endswith("grind")
        return {"errors": [] if ok else ["no"], "sorry_count": 0}

    got = ns.batched_sweep_prover(check_fn, ())(PROBE)
    assert "first" not in got["lean_text"]
    assert got["lean_text"].rstrip().endswith("grind")


def test_batched_prover_fails_open_on_daemon_trouble():
    def check_fn(code):
        return {"error": "connection refused", "errors": ["connection refused"]}

    got = ns.batched_sweep_prover(check_fn, ("gainToPain",))(PROBE)
    assert got["lean_text"] == PROBE


def _dead_daemon():
    def check_fn(code):
        return {"error": "connection refused", "errors": ["connection refused"]}

    def prove_fn(probe):
        return {"lean_text": probe, "tokens": 0}
    return check_fn, prove_fn


def test_an_entry_that_only_hit_daemon_errors_is_not_done(tmp_path):
    """Otherwise an outage is permanent: the entry is recorded, marked done, and never
    probed again however many times the run is resumed."""
    import json as _j
    p = tmp_path / "out.jsonl"
    p.write_text("\n".join(_j.dumps(r) for r in [
        {"arm": "catalogue", "entry_id": "good", "verdict": "power_control"},
        {"arm": "catalogue", "entry_id": "good", "verdict": "not_shown_unnecessary"},
        {"arm": "catalogue", "entry_id": "lost", "verdict": "power_control"},
        {"arm": "catalogue", "entry_id": "lost", "verdict": "daemon_error"},
    ]) + "\n", encoding="utf-8")
    assert ns.done_keys(str(p)) == {("catalogue", "good")}


def test_run_sweep_stops_instead_of_burning_the_queue_on_an_outage(tmp_path):
    """A dead daemon answers instantly, so without this the run races through every
    remaining entry writing daemon_error and calls itself finished."""
    check_fn, prove_fn = _dead_daemon()
    entries = [ns.Entry("catalogue", f"e{i}", "d", "gainToPain_nonneg_of_denom_pos",
                        "full", "human", GUARDED) for i in range(50)]
    stats = ns.run_sweep(entries, str(tmp_path / "out.jsonl"), check_fn=check_fn,
                         prove_fn=prove_fn, regate_fn=lambda c: {"passed": True},
                         log=lambda m: None)
    assert stats["entries"] <= 5           # stopped early, did not consume all 50
    assert stats["aborted"] == "daemon_unreachable"


def test_a_healthy_run_does_not_abort(tmp_path):
    check_fn, prove_fn = _fakes({"h"})
    entries = [ns.Entry("catalogue", f"e{i}", "d", "gainToPain_nonneg_of_denom_pos",
                        "full", "human", GUARDED) for i in range(6)]
    stats = ns.run_sweep(entries, str(tmp_path / "out.jsonl"), check_fn=check_fn,
                         prove_fn=prove_fn, regate_fn=lambda c: {"passed": True},
                         log=lambda m: None)
    assert stats["entries"] == 6 and stats["aborted"] is None


def test_a_dead_daemon_is_not_mistaken_for_a_blind_entry():
    """The dangerous confusion. A blind entry is data — it is never re-run and it
    raises the reported blind fraction. A dead daemon must therefore never look like
    one, or an outage silently becomes the result."""
    check_fn, prove_fn = _dead_daemon()
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert {r["verdict"] for r in recs} == {"daemon_error"}
    assert "power_control" not in {r["verdict"] for r in recs}


def test_a_live_daemon_that_simply_cannot_prove_it_is_blind():
    check_fn, prove_fn = _blind_fakes()      # answers fine, just closes nothing
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    recs = ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                          regate_fn=lambda c: {"passed": True})
    assert recs[0]["verdict"] == "power_control"
    assert recs[0]["sweep_proves_original"] is False


LIB_SRC = '''import Mathlib
import MathFin.Basic

open MeasureTheory
open scoped NNReal

namespace MathFin

variable {ι : Type*} (s : Finset ι)

/-- doc -/
theorem gainToPain_nonneg (r : ι → ℝ) (h : 0 < ∑ i ∈ s, negPart (r i)) :
    0 ≤ gainToPain s r := by
  positivity

theorem long_one (r : ι → ℝ) (h : True) : 0 ≤ 1 := by
  have a := 1
  have b := 2
  have c := 3
  have d := 4
  have e := 5
  have f := 6
  have g := 7
  have i := 8
  have j := 9
  have k := 10
  norm_num

end MathFin
'''


def _lib(tmp_path):
    d = tmp_path / "MathFin" / "Performance"
    d.mkdir(parents=True)
    (d / "Ratios.lean").write_text(LIB_SRC, encoding="utf-8")
    return str(tmp_path)


def test_library_probe_imports_the_module_and_reopens_its_context(tmp_path):
    got = {e.thm: e for e in ns.load_library_entries(PACK, _lib(tmp_path))}
    e = got["gainToPain_nonneg" + ns.PROBE_SUFFIX]
    assert e.code.startswith("import MathFin.Performance.Ratios")
    assert "open MeasureTheory" in e.code
    assert "open scoped NNReal" in e.code
    assert "namespace MathFin" in e.code and e.code.rstrip().endswith("end MathFin")
    assert "variable {ι : Type*} (s : Finset ι)" in e.code
    assert e.domain == "MathFin.Performance.Ratios" and e.arm == "library"


def test_library_declaration_is_renamed_so_it_cannot_clash_with_the_import(tmp_path):
    """The probe imports the module that already defines this theorem. Re-declaring the
    same name inside the same namespace is an error, so the probe carries a fresh one."""
    e = {x.thm: x for x in ns.load_library_entries(PACK, _lib(tmp_path))}[
        "gainToPain_nonneg" + ns.PROBE_SUFFIX]
    assert "theorem gainToPain_nonneg " not in e.code
    assert ns.primary_decl(e.code) == e.thm
    # the real lemma the paper has to name is recoverable from the probe alias
    assert e.thm[:-len(ns.PROBE_SUFFIX)] == "gainToPain_nonneg"


def test_library_entries_are_stratified_by_proof_shape(tmp_path):
    got = {x.entry_id: x for x in ns.load_library_entries(PACK, _lib(tmp_path))}
    statuses = {k.rsplit(".", 1)[-1]: v.status for k, v in got.items()}
    assert statuses["gainToPain_nonneg_necessity_probe"] == "tactic_short"
    assert statuses["long_one_necessity_probe"] == "tactic_long"


def test_library_binders_are_pre_filtered_the_same_way(tmp_path):
    e = {x.thm: x for x in ns.load_library_entries(PACK, _lib(tmp_path))}[
        "gainToPain_nonneg" + ns.PROBE_SUFFIX]
    assert ns.probe_worthy_binders(e.code, e.thm) == ["h"]


LIB_DOC_SRC = '''import Mathlib

namespace MathFin

variable {ι : Type*}

/-- first -/
theorem one (h : True) : 0 ≤ 1 := by norm_num

/-- second, and this docstring belongs to `two`, not to `one` -/
@[simp]
theorem two (h : True) : 0 ≤ 2 := by norm_num

end MathFin
'''


def test_a_declaration_does_not_swallow_the_next_ones_docstring(tmp_path):
    """A `/-- ... -/` with no declaration after it is a Lean syntax error, so leaving
    the next declaration's docstring on the end of this one breaks every probe built
    from it — and it would look like the theorem failing to elaborate."""
    d = tmp_path / "MathFin"
    d.mkdir(parents=True)
    (d / "A.lean").write_text(LIB_DOC_SRC, encoding="utf-8")
    got = {e.thm: e for e in ns.load_library_entries(PACK, str(tmp_path))}
    one = got["one" + ns.PROBE_SUFFIX]
    assert "second" not in one.code
    assert "@[simp]" not in one.code
    assert one.code.rstrip().endswith("end MathFin")


def test_context_keeps_source_order_around_the_namespace(tmp_path):
    """`variable` written inside a namespace must stay inside it: hoisted out, a binder
    whose type is namespace-local stops resolving."""
    d = tmp_path / "MathFin"
    d.mkdir(parents=True)
    (d / "A.lean").write_text(LIB_DOC_SRC, encoding="utf-8")
    code = {e.thm: e for e in ns.load_library_entries(PACK, str(tmp_path))}[
        "one" + ns.PROBE_SUFFIX].code
    assert code.index("namespace MathFin") < code.index("variable {ι : Type*}")


def test_library_loader_stays_inside_the_package(tmp_path):
    """A checkout holds more Lean than the library: vendored upstream sources, exercise
    files, tests. Their modules do not resolve as imports and they are not the library
    under study, so a rate over them would be a rate over the wrong thing."""
    (tmp_path / "MathFin").mkdir()
    (tmp_path / "MathFin" / "A.lean").write_text(
        "theorem mine (h : True) : 0 ≤ 1 := by norm_num\n", encoding="utf-8")
    (tmp_path / "upstream").mkdir()
    (tmp_path / "upstream" / "B.lean").write_text(
        "theorem theirs (h : True) : 0 ≤ 1 := by norm_num\n", encoding="utf-8")
    got = ns.load_library_entries(PACK, str(tmp_path))
    assert [e.domain for e in got] == ["MathFin.A"]


def test_a_batched_timeout_falls_back_instead_of_becoming_a_negative():
    """The daemon kills the REPL at LEAN_ELAB_TIMEOUT (180 s), and batching concentrates
    eight tactics into one elaboration, so it runs into that cap on exactly the hard
    theorems. Treating the kill as "nothing closed it" would manufacture false negatives
    that no all-negative A/B could ever detect."""
    seen = []

    def check_fn(code):
        seen.append(code)
        if "first" in code:
            return {"error": "elaboration timed out after 180.0s (REPL killed)",
                    "errors": ["elaboration timed out"]}
        # per tactic, `positivity` closes it
        ok = code.rstrip().endswith("positivity")
        return {"errors": [] if ok else ["no"], "sorry_count": 0}

    got = ns.batched_sweep_prover(check_fn, ())(PROBE)
    assert len(seen) > 1, "a timed-out batch must fall back, not conclude"
    assert "sorry" not in got["lean_text"]
    assert got["lean_text"].rstrip().endswith("positivity")


def _lib_file(tmp_path):
    d = tmp_path / "MathFin" / "Performance"
    d.mkdir(parents=True)
    (d / "Ratios.lean").write_text(LIB_SRC, encoding="utf-8")
    return str(tmp_path)


def test_a_probe_imports_its_own_module_by_default(tmp_path):
    """The faithful environment: the module the declaration was elaborated against. A
    shared root header was tried to keep the REPL warm across 149 modules and measured
    worthless — the REPL respawns on nearly every call, so nothing is ever warm, and no
    import shape was cheaper."""
    got = ns.load_library_entries(PACK, _lib_file(tmp_path))
    assert {e.code.splitlines()[0] for e in got} == {"import MathFin.Performance.Ratios"}
    assert {e.domain for e in got} == {"MathFin.Performance.Ratios"}


def test_a_shared_root_header_is_still_available(tmp_path):
    got = ns.load_library_entries(PACK, _lib_file(tmp_path), import_root="MathFin")
    assert {e.code.splitlines()[0] for e in got} == {"import MathFin"}


LIB_TRAPS_SRC = '''import Mathlib

namespace MathFin

/-- A docstring whose prose starts a line with a keyword:
theorem for ±1 walks, combined with the bijection above.
-/
theorem real_one (h : True) : 0 ≤ 1 := by norm_num

private lemma helper_one (h : True) : 0 ≤ 2 := by norm_num

end MathFin
'''


def _traps(tmp_path):
    d = tmp_path / "MathFin"
    d.mkdir(parents=True)
    (d / "T.lean").write_text(LIB_TRAPS_SRC, encoding="utf-8")
    return str(tmp_path)


def test_prose_inside_a_docstring_is_not_a_declaration(tmp_path):
    """`theorem for ±1 walks` inside a doc comment is English, not Lean. Matching it
    invents a declaration named `for` whose probe cannot elaborate — which the sweep
    would then record as the theorem being unprovable."""
    got = ns.load_library_entries(PACK, _traps(tmp_path))
    assert "for" + ns.PROBE_SUFFIX not in {e.thm for e in got}


def test_private_declarations_are_probed_now_that_the_locator_parses_them(tmp_path):
    """They used to be dropped because the shared locator rejected the modifier and they
    would have arrived as blind entries. Fixed at the source, so they are population."""
    got = {e.thm for e in ns.load_library_entries(PACK, _traps(tmp_path))}
    assert "helper_one" + ns.PROBE_SUFFIX in got
    assert "real_one" + ns.PROBE_SUFFIX in got


def test_every_emitted_entry_is_locatable_by_the_shared_parser(tmp_path):
    """The invariant that keeps extraction failure from masquerading as blindness."""
    from autoformalize import _locate_named
    for e in ns.load_library_entries(PACK, _traps(tmp_path)):
        _locate_named(e.code, e.thm)      # raises if the loader emitted a dud


def test_a_negative_records_the_reduced_statement_it_could_not_close():
    """`not_shown_unnecessary` is the sweep's most common verdict and its least
    informative: the prober fails closed, so it cannot distinguish "the binder is
    load-bearing" from "the eight-tactic sweep could not reach the reduced statement".
    Those negatives are the input to any downstream triage, and the probe text is
    already built — recording it costs nothing at run time and the run is expensive."""
    check_fn, prove_fn = _fakes(set())          # closes nothing that was reduced
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    rec = [r for r in ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                                     regate_fn=lambda c: {"passed": True})
           if r["binder"] == "h"][0]
    assert rec["verdict"] == "not_shown_unnecessary"
    assert "sorry" in rec["reduced_statement"]
    assert "(h :" not in rec["reduced_statement"]      # the binder really is dropped


def test_a_certified_positive_records_its_reduced_statement_too():
    """A positive is the paper's kernel-certified claim; it has to be reproducible from
    the record without re-running the sweep."""
    check_fn, prove_fn = _fakes({"h"})
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    rec = [r for r in ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                                     regate_fn=lambda c: {"passed": True})
           if r["binder"] == "h"][0]
    assert rec["verdict"] == "certified_unnecessary"
    assert "sorry" in rec["reduced_statement"]


def test_records_with_no_probe_carry_an_empty_reduced_statement():
    """power_control, and a blind entry's short-circuited binders, never built one."""
    check_fn, prove_fn = _blind_fakes()
    e = ns.Entry("catalogue", "e1", "d", "gainToPain_nonneg_of_denom_pos", "full",
                 "human", GUARDED)
    for r in ns.sweep_entry(e, check_fn=check_fn, prove_fn=prove_fn,
                            regate_fn=lambda c: {"passed": True}):
        assert r["reduced_statement"] == ""
