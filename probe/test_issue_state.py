"""The issue is the state machine; these pin the two ways that can go wrong.

Writing state INTO the source is what removes the drift between five caches. But a
writer that fails loudly would make bookkeeping able to fail a tick, and a reconciler
that guesses would overrule human triage. Both are tested here, and both matter more
than the happy path.
"""
from __future__ import annotations

import json
import os
import tempfile

import issue_state as S


class R:
    """A `gh` stand-in. `script` maps a subcommand marker to (rc, stdout)."""

    def __init__(self, script, fail=False):
        self.script, self.fail, self.calls = script, fail, []

    def __call__(self, argv):
        self.calls.append(argv)
        if self.fail:
            raise OSError("gh: not found")
        for marker, (rc, out) in self.script.items():
            if marker in argv:
                return type("P", (), {"returncode": rc, "stdout": out, "stderr": ""})()
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()


def _labels(*names):
    return json.dumps({"labels": [{"name": n} for n in names]})


# --- fail-open: bookkeeping may never fail a tick -----------------------------

def test_unreachable_gh_is_false_not_an_exception():
    assert S.set_status("o/n", 1, "status:review", run_fn=R({}, fail=True)) is False


def test_a_rejected_write_is_reported_not_swallowed_silently():
    """The one thing it must never do is look like it worked."""
    said = []
    ok = S.set_status("o/n", 1, "status:review", log=said.append,
                      run_fn=R({"view": (0, _labels("status:ready")),
                                "edit": (1, "HTTP 403: Resource not accessible")}))
    assert ok is False
    assert any("could not set" in m for m in said)


def test_unknown_status_is_refused_before_any_network_call():
    r = R({})
    assert S.set_status("o/n", 1, "status:done", run_fn=r) is False
    assert r.calls == []


def test_unparseable_view_output_is_no_status_not_a_crash():
    assert S.current_status("o/n", 1, run_fn=R({"view": (0, "<html>502</html>")})) is None


# --- the label swap -----------------------------------------------------------

def test_the_old_status_is_removed_as_the_new_one_lands():
    """Two status labels at once is a worse state than the one being fixed."""
    r = R({"view": (0, _labels("status:ready", "type:proof")), "edit": (0, "")})
    assert S.set_status("o/n", 82, "status:in-progress", run_fn=r) is True
    edit = next(c for c in r.calls if "edit" in c)
    assert "--add-label" in edit and "status:in-progress" in edit
    assert "--remove-label" in edit and "status:ready" in edit


def test_a_no_op_move_is_success_and_writes_nothing():
    r = R({"view": (0, _labels("status:review"))})
    assert S.set_status("o/n", 82, "status:review", run_fn=r) is True
    assert not any("edit" in c for c in r.calls)


def test_an_issue_with_no_status_yet_gets_one_without_a_removal():
    r = R({"view": (0, _labels("type:proof")), "edit": (0, "")})
    assert S.set_status("o/n", 82, "status:in-progress", run_fn=r) is True
    assert "--remove-label" not in next(c for c in r.calls if "edit" in c)


# --- what the witnesses imply -------------------------------------------------

def test_an_open_pr_outranks_a_staged_stub():
    """Both are true at once — the PR was cut from the stub — and review is later."""
    assert S.desired_status(5, pr_numbers={5}, queue_ids={5}) == "status:review"


def test_a_stub_with_no_pr_is_in_progress_and_nothing_is_ready():
    assert S.desired_status(5, pr_numbers=set(), queue_ids={5}) == "status:in-progress"
    assert S.desired_status(5, pr_numbers=set(), queue_ids=set()) == "status:ready"


def test_reconcile_finds_the_stranded_targets():
    """The outage, reconstructed: six stubs staged, every issue still `ready`, so the
    refill kept re-drafting work that was already in the queue."""
    issues = [{"number": n, "labels": [{"name": "status:ready"}]}
              for n in (56, 57, 93)]
    plan = S.reconcile_plan(issues, pr_numbers=set(), queue_ids={56, 57, 93})
    assert [p["want"] for p in plan] == ["status:in-progress"] * 3


def test_reconcile_returns_a_merged_target_to_ready():
    """The stub was retired after the merge; the label has to follow it back."""
    issues = [{"number": 66, "labels": [{"name": "status:in-progress"}]}]
    plan = S.reconcile_plan(issues, pr_numbers=set(), queue_ids=set())
    assert plan == [{"number": 66, "now": "status:in-progress", "want": "status:ready"}]


def test_reconcile_is_silent_when_everything_agrees():
    issues = [{"number": 1, "labels": [{"name": "status:review"}]},
              {"number": 2, "labels": [{"name": "status:in-progress"}]},
              {"number": 3, "labels": [{"name": "status:ready"}]}]
    assert S.reconcile_plan(issues, pr_numbers={1}, queue_ids={2}) == []


# --- what it must NOT do ------------------------------------------------------

def test_a_human_status_is_never_overruled():
    """`status:blocked-design` means a person decided the target consumes a primitive
    that does not exist. "No PR and no stub" is exactly what that looks like from
    here, so a naive reconcile flips it to `ready` and feeds the refill the
    `needs_primitives` death family on a loop. formal-econometrics has two of these
    (FWL, OVB) waiting on the projection layer."""
    for human in ("status:blocked-design", "status:blocked-upstream",
                  "status:needs-triage", "status:needs-info"):
        issues = [{"number": 9, "labels": [{"name": human}]}]
        assert S.reconcile_plan(issues, pr_numbers=set(), queue_ids=set()) == [], human


def test_an_unlabelled_issue_is_never_promoted_into_the_backlog():
    """This repairs the pipeline's own writes; it never creates work. A target enters
    the backlog when a human labels it ready, not because nothing was blocking it."""
    issues = [{"number": 9, "labels": [{"name": "type:proof"}]}, {"number": 10}]
    assert S.reconcile_plan(issues, pr_numbers=set(), queue_ids=set()) == []


def test_a_contradictory_pair_is_left_for_a_human():
    issues = [{"number": 9, "labels": [{"name": "status:ready"},
                                       {"name": "status:review"}]}]
    assert S.reconcile_plan(issues, pr_numbers=set(), queue_ids=set()) == []


def test_a_failed_pr_lookup_refuses_to_reconcile():
    """"No open PRs" and "could not ask" must not reconcile the same way: reading a
    failed lookup as zero would demote every issue under review and re-open it all to
    the refill in one sweep."""
    assert S.claimed_issue_numbers("o/n", run_fn=R({"pr": (1, "gone")})) is None
    said = []
    r = R({"pr": (1, "HTTP 502")})
    assert S.reconcile("o/n", "/nonexistent", run_fn=r, log=said.append) == []
    assert any("refusing to reconcile" in m for m in said)
    assert not any("issue" in c and "list" in c for c in r.calls)


def test_reconcile_defaults_to_reporting_not_writing():
    r = R({"pr": (0, "[]"),
           "issue": (0, json.dumps([{"number": 9,
                                     "labels": [{"name": "status:in-progress"}]}]))})
    plan = S.reconcile("o/n", "/nonexistent", run_fn=r)
    assert plan and not any("edit" in c for c in r.calls)


# --- the witnesses themselves -------------------------------------------------

def test_staged_reads_stubs_and_ignores_the_sidecars():
    with tempfile.TemporaryDirectory() as d:
        for f in ("cal-bk-56.lean", "cal-bk-129.lean", "cal-bk-56.entry.json",
                  "README.md", "manifest.json"):
            open(os.path.join(d, f), "w").write("x")
        assert S.staged_issue_numbers(d) == {56, 129}


def test_staged_on_a_missing_dir_is_empty_not_an_error():
    assert S.staged_issue_numbers("/nonexistent/queue") == set()


def test_only_a_closing_keyword_claims_an_issue():
    """A PR that merely mentions the issue in prose does not claim it."""
    rows = json.dumps([{"number": 1, "title": "feat: bounds", "body": "Closes #82"},
                       {"number": 2, "title": "wip", "body": "see #99 for context"},
                       {"number": 3, "title": "fixes #100", "body": ""}])
    assert S.claimed_issue_numbers("o/n", run_fn=R({"pr": (0, rows)})) == {82, 100}
