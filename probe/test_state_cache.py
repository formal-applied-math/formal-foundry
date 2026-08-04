"""Pure tests for the proof-state cache + its recurrence measurement. No daemon."""
from __future__ import annotations

import json

import state_cache as sc


def test_put_get_roundtrip_persists(tmp_path):
    p = str(tmp_path / "state-cache.json")
    c = sc.StateCache(p)
    assert c.get("k1") is None
    c.put("k1", tactic="simp", state="⊢ P", target="cal-bk-1")
    assert c.get("k1") == "simp"
    assert sc.StateCache(p).get("k1") == "simp"          # a fresh instance reads the file


def test_generation_bump_invalidates_the_file(tmp_path):
    """A tactic proved against the old Mathlib may not close the same goal after a pin
    bump, so a bump drops the store wholesale rather than serving stale suggestions."""
    p = str(tmp_path / "c.json")
    sc.StateCache(p).put("k", tactic="simp", state="⊢ P", target="t")
    c = sc.StateCache(p, generation="different")
    assert c.get("k") is None


def test_tolerates_corrupt_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("not json{", encoding="utf-8")
    c = sc.StateCache(str(p))
    assert c.get("k") is None
    c.put("k", tactic="ring", state="⊢ P", target="t")
    assert sc.StateCache(str(p)).get("k") == "ring"


def test_stats_track_hits_and_misses(tmp_path):
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("k", tactic="simp", state="⊢ P", target="t")
    c.get("k"); c.get("k"); c.get("absent")
    assert c.stats["entries"] == 1 and c.stats["hits"] == 2 and c.stats["misses"] == 1


# --- the measurement: does the repeated work actually exist? ----------------

def test_recurrence_counts_every_sighting_and_the_targets_behind_them(tmp_path):
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("k", tactic="simp", state="⊢ P", target="cal-bk-1")
    c.put("k", tactic="simp", state="⊢ P", target="cal-bk-2")
    c.put("k", tactic="simpa", state="⊢ P", target="cal-bk-2")
    e = c.entry("k")
    assert e["seen"] == 3
    assert e["targets"] == ["cal-bk-1", "cal-bk-2"]      # deduped, insertion-ordered


def test_first_tactic_wins_so_a_proved_suggestion_is_not_overwritten(tmp_path):
    """Both tactics closed the state, so either is serviceable; keeping the first makes
    the store stable run to run (a churning suggestion is noise in the context pack)."""
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("k", tactic="simp", state="⊢ P", target="a")
    c.put("k", tactic="ring", state="⊢ P", target="b")
    assert c.get("k") == "simp"


def test_report_separates_cross_target_recurrence_from_within_target(tmp_path):
    """The discriminating number. A state seen 5 times inside ONE target is just a loop
    in one proof; a state seen across TWO targets is reusable work, which is the only
    thing that justifies the cache."""
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("solo", tactic="simp", state="⊢ A", target="t1")
    c.put("within", tactic="ring", state="⊢ B", target="t1")
    c.put("within", tactic="ring", state="⊢ B", target="t1")
    c.put("across", tactic="norm_num", state="⊢ C", target="t1")
    c.put("across", tactic="norm_num", state="⊢ C", target="t2")
    r = c.report()
    assert r["distinct_states"] == 3
    assert r["total_sightings"] == 5
    assert r["recurring_states"] == 2                    # seen more than once
    assert r["cross_target_states"] == 1                 # the number that matters
    assert r["cross_target_keys"] == ["across"]


def test_report_of_an_empty_store_is_all_zeros(tmp_path):
    r = sc.StateCache(str(tmp_path / "c.json")).report()
    assert r["distinct_states"] == 0 and r["cross_target_states"] == 0
    assert r["cross_target_keys"] == []


def test_ingest_records_extracted_pairs_and_returns_the_new_ones(tmp_path):
    c = sc.StateCache(str(tmp_path / "c.json"))
    pairs = [{"key": "k1", "state": "⊢ P", "tactic": "simp"},
             {"key": "k2", "state": "⊢ Q", "tactic": "ring"}]
    assert c.ingest(pairs, target="t1") == 2             # both new
    assert c.ingest(pairs, target="t2") == 0             # both already known
    assert c.report()["cross_target_states"] == 2


def test_suggestions_render_only_cross_target_states(tmp_path):
    """What goes into the prover's context pack: states that recurred ACROSS targets are
    evidence of reusable work. A state seen once is just this proof's own history."""
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("a", tactic="simp", state="⊢ A", target="t1")
    c.put("b", tactic="ring", state="⊢ B", target="t1")
    c.put("b", tactic="ring", state="⊢ B", target="t2")
    text = c.suggestions()
    assert "⊢ B" in text and "ring" in text
    assert "⊢ A" not in text


def test_suggestions_are_empty_when_nothing_has_recurred(tmp_path):
    c = sc.StateCache(str(tmp_path / "c.json"))
    c.put("a", tactic="simp", state="⊢ A", target="t1")
    assert c.suggestions() == ""


def test_store_is_json_and_carries_its_generation(tmp_path):
    p = tmp_path / "c.json"
    sc.StateCache(str(p)).put("k", tactic="simp", state="⊢ P", target="t")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["generation"] == sc.CACHE_GENERATION
    assert data["entries"]["k"]["tactic"] == "simp"
