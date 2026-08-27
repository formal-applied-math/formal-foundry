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
