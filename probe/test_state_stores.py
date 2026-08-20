"""Five stores answer "what work exists and what is done"; disagreement was silent.

  1. GitHub issues            — the source: what a human wants proved
  2. targets/queue/*.lean     — which issues became a gated Lean statement
  3. targets/queue/manifest   — which of those are activated (DERIVED from 2)
  4. pipeline_state.json      — attempted_issues AND history (the SAME fact, twice)
  5. open PRs on the target   — external ground truth

Three of tonight's outages were disagreements between them, every one silent:
a stale manifest (3 vs 2) reporting `no_unattempted_targets` over six unattempted
targets; `.entry.json` conflating *drafted* with *done*; and merged stubs left in 2.
The duplicate-PR incident of 2026-07-20 was a disagreement inside 4.

These tests pin the two fixes: the duplicated fact is read from both witnesses, and a
null selection explains itself.
"""
from __future__ import annotations

import os
import tempfile

import pipeline_lib as P


# --- 4: one fact, two records ------------------------------------------------

def test_attempted_reads_both_records():
    state = {"attempted_issues": ["a"], "history": [{"id": "b"}]}
    assert P.attempted_ids(state) == {"a", "b"}


def test_losing_attempted_issues_no_longer_loses_the_answer():
    """A reconstruction of the 2026-07-20 incident. `attempted_issues` was truncated;
    `history` still held every id; nothing asked it, so #161/#162 were re-drafted and
    the pipeline opened duplicate PRs for both."""
    intact = {"attempted_issues": ["cal-bk-161", "cal-bk-162"],
              "history": [{"id": "cal-bk-161"}, {"id": "cal-bk-162"}]}
    truncated = {"attempted_issues": [], "history": intact["history"]}
    cands = [{"id": "cal-bk-161"}, {"id": "cal-bk-162"}]

    assert P.next_target(cands, intact, claimed_fn=lambda c: False) is None
    assert P.next_target(cands, truncated, claimed_fn=lambda c: False) is None, \
        "history alone must still guard against re-attempting"


def test_losing_history_also_no_longer_loses_the_answer():
    """Symmetric: the union has to work in both directions or it is not a fix."""
    state = {"attempted_issues": ["cal-bk-161"], "history": []}
    assert P.next_target([{"id": "cal-bk-161"}], state, claimed_fn=lambda c: False) is None


def test_the_union_cannot_change_behaviour_when_they_agree():
    """Which is always, since record_attempt writes both. This is what makes the
    change safe to ship on a pipeline that is finally working."""
    state = {"attempted_issues": ["a", "b"],
             "history": [{"id": "a"}, {"id": "b"}]}
    assert P.attempted_ids(state) == set(state["attempted_issues"])
    got = P.next_target([{"id": "a"}, {"id": "c"}], state, claimed_fn=lambda c: False)
    assert got["id"] == "c"


# --- the null result must explain itself -------------------------------------

def test_census_attributes_every_exclusion():
    state = {"attempted_issues": ["a"], "history": []}
    cands = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    c = P.selection_census(cands, state, claimed_fn=lambda x: x["id"] == "b")
    assert c["candidates"] == 3
    assert c["excluded_attempted"] == 1
    assert c["excluded_claimed"] == 1
    assert c["selectable"] == 1


def test_census_detects_the_stale_manifest_signature():
    """The exact outage: nine stubs on disk, four in the candidate list, and a skip
    that blamed 'no unattempted targets'."""
    with tempfile.TemporaryDirectory() as d:
        for t in ("cal-bk-56", "cal-bk-71", "cal-bk-129"):
            open(os.path.join(d, f"{t}.lean"), "w").write("theorem t : True := by sorry")
        cands = [{"id": "cal-bk-56"}]          # a manifest that has fallen behind
        c = P.selection_census(cands, {"attempted_issues": []},
                               claimed_fn=lambda x: False, queue_dir=d)
        assert c["stubs_on_disk"] == 3
        assert c["missing_from_candidates"] == ["cal-bk-129", "cal-bk-71"]


def test_census_is_quiet_when_the_manifest_is_current():
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "cal-bk-56.lean"), "w").write("theorem t : True := by sorry")
        c = P.selection_census([{"id": "cal-bk-56"}], {"attempted_issues": []},
                               claimed_fn=lambda x: False, queue_dir=d)
        assert c["missing_from_candidates"] == []


def test_census_survives_a_raising_backstop():
    """`pr_claimed` shells out to `gh`; an unreachable GitHub must never make the
    diagnostic itself the failure."""
    def boom(_c):
        raise RuntimeError("no network")

    c = P.selection_census([{"id": "a"}], {"attempted_issues": []}, claimed_fn=boom)
    assert c["candidates"] == 1 and c["excluded_claimed"] == 0
