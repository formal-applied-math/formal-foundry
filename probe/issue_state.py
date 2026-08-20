"""The ISSUE is the state machine. Everything else is a cache of it.

Five stores answered "what work exists and what is done" — GitHub issues, the queue
stubs, `manifest.json`, `pipeline_state.json`, and open PRs — and a disagreement
between any two of them was silent. Three outages in one night came from that: a
stale manifest reporting `no_unattempted_targets` over six unattempted targets, an
`.entry.json` conflating *drafted* with *done*, and merged stubs left in the queue.

The root cause is not the number of caches. It is that the SOURCE never recorded
anything. The pipeline read issues and never wrote to them, so it had to keep private
bookkeeping to avoid redoing work, and private bookkeeping is what drifts.
`next_target`'s own docstring named the consequence: "a passing tick leaves the issue
`status:ready` until a human merges, so every target awaiting review is re-selectable
for the whole window."

The labels to fix it already existed and were unused by the pipeline:

    status:ready        available — the refill may draft it
    status:in-progress  a gated stub is staged in the queue
    status:review       a PR is open against it
    (closed)            merged; terminal

With the source carrying its own state, `select_issues`'s existing `status:ready`
filter does the de-duplication that `_already_seeded`, `attempted_issues` and
`queue_claimed` were each separately approximating — and a lost local cache costs a
rebuild rather than a duplicate PR.

FAIL-OPEN, always. A label write is bookkeeping; the proof is the work. No missing
scope, rate limit, or unreachable GitHub may fail a tick, so every entry point here
returns a bool and swallows everything. The cost of a missed transition is one
redundant draft, which the local caches still catch — they stop being load-bearing,
they do not stop existing.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

__all__ = ["STATUSES", "PIPELINE_STATUSES", "set_status", "current_status",
           "desired_status", "reconcile_plan"]

# every `status:` label in the vocabulary — one must be removed as another is added,
# or an issue ends up claiming two states at once.
STATUSES = (
    "status:ready",
    "status:in-progress",
    "status:review",
    "status:needs-triage",
    "status:needs-info",
    "status:blocked-design",
    "status:blocked-upstream",
)


# The three the pipeline writes. The rest — needs-triage, needs-info,
# blocked-design, blocked-upstream — are human judgments about whether the work is
# well-posed at all, and no amount of machine evidence overrules one.
PIPELINE_STATUSES = ("status:ready", "status:in-progress", "status:review")


def desired_status(number: int, *, pr_numbers, queue_ids) -> str:
    """What the issue's status SHOULD be, given the two witnesses.

    An open PR outranks a staged stub: both can be true at once (the stub is what the
    PR was cut from) and `review` is the later state."""
    if number in pr_numbers:
        return "status:review"
    if number in queue_ids:
        return "status:in-progress"
    return "status:ready"


def reconcile_plan(issues: list[dict], *, pr_numbers, queue_ids) -> list[dict]:
    """The label writes needed to make the issues agree with the witnesses.

    `issues` is gh's shape: `[{"number": N, "labels": [{"name": ...}]}]`. Returns one
    `{number, now, want}` per DISAGREEMENT, empty when everything already lines up.

    Two rules keep this from doing damage, and both are load-bearing:

    * an issue holding a human status is skipped entirely. `status:blocked-design`
      means a person decided the target consumes a primitive that does not exist yet;
      "no PR and no stub" is exactly what that looks like from here, so a naive
      reconcile would flip every blocked issue to `ready` and feed the refill the
      `needs_primitives` death family on a loop.
    * an issue holding NO status is skipped too. This repairs the pipeline's own
      writes; it never creates work. A target enters the backlog when a human labels
      it `status:ready`, not because a reconcile found nothing blocking it.
    """
    plan = []
    for issue in issues:
        n = issue.get("number")
        names = {lab["name"] if isinstance(lab, dict) else str(lab)
                 for lab in (issue.get("labels") or [])}
        held = [s for s in STATUSES if s in names]
        if len(held) != 1 or held[0] not in PIPELINE_STATUSES:
            continue          # human status, no status, or a contradictory pair
        want = desired_status(n, pr_numbers=pr_numbers, queue_ids=queue_ids)
        if want != held[0]:
            plan.append({"number": n, "now": held[0], "want": want})
    return plan


def _gh(args: list[str], run_fn=None) -> tuple[bool, str]:
    """Run `gh`, returning (ok, output). Never raises."""
    runner = run_fn or (lambda a: subprocess.run(a, capture_output=True, text=True,
                                                 timeout=60))
    try:
        r = runner(["gh", *args])
    except Exception as e:  # noqa: BLE001 — bookkeeping must not fail a tick
        return False, f"{type(e).__name__}: {e}"
    ok = getattr(r, "returncode", 1) == 0
    return ok, (getattr(r, "stdout", "") or getattr(r, "stderr", "") or "")


def current_status(slug: str, number: int, *, run_fn=None) -> str | None:
    """The issue's current `status:` label, or None if it has none / lookup failed."""
    ok, out = _gh(["issue", "view", str(number), "--repo", slug,
                   "--json", "labels"], run_fn=run_fn)
    if not ok:
        return None
    try:
        labels = {lab["name"] for lab in json.loads(out).get("labels", [])}
    except (ValueError, KeyError, TypeError):
        return None
    for s in STATUSES:
        if s in labels:
            return s
    return None


def set_status(slug: str, number: int, status: str, *, run_fn=None,
               log=lambda m: None) -> bool:
    """Move an issue to `status`, removing whichever status label it carries now.

    Returns True only on a confirmed write. Never raises: a missing `issues: write`
    scope, a rate limit or an unreachable GitHub all return False and log, because
    the proof is the work and this is bookkeeping about it.
    """
    if status not in STATUSES:
        log(f"refusing unknown status {status!r} (known: {', '.join(STATUSES)})")
        return False

    now = current_status(slug, number, run_fn=run_fn)
    if now == status:
        return True                     # already there; not a failure

    args = ["issue", "edit", str(number), "--repo", slug, "--add-label", status]
    if now:
        args += ["--remove-label", now]
    ok, out = _gh(args, run_fn=run_fn)
    if ok:
        log(f"#{number}: {now or '(none)'} → {status}")
    else:
        # The one thing this must never do is look like it worked.
        log(f"#{number}: could not set {status} ({out.strip()[:120]}) — "
            "continuing; the local queue still guards against a duplicate draft")
    return ok


def staged_issue_numbers(queue_dir: str) -> set[int]:
    """Issue numbers with a stub staged in the queue — witness for `in-progress`."""
    out = set()
    try:
        names = os.listdir(queue_dir)
    except OSError:
        return out
    for f in names:
        m = re.fullmatch(r"cal-bk-(\d+)\.lean", f)
        if m:
            out.add(int(m.group(1)))
    return out


def claimed_issue_numbers(slug: str, *, run_fn=None) -> set[int] | None:
    """Issue numbers closed by an OPEN PR — witness for `review`.

    None (not the empty set) when the lookup failed, because "no PRs" and "could not
    ask" must not reconcile the same way: reading a failed lookup as zero open PRs
    would demote every issue under review back to `in-progress` and re-open it to the
    refill."""
    ok, out = _gh(["pr", "list", "--repo", slug, "--state", "open",
                   "--json", "number,body,title", "--limit", "200"], run_fn=run_fn)
    if not ok:
        return None
    try:
        rows = json.loads(out.strip() or "[]")
    except ValueError:
        return None
    closes = re.compile(r"(?i)\b(?:closes|fixes|resolves)\s+#(\d+)\b")
    nums = set()
    for r in rows:
        nums |= {int(m) for m in
                 closes.findall((r.get("body") or "") + " " + (r.get("title") or ""))}
    return nums


def reconcile(slug: str, queue_dir: str, *, apply: bool = False, run_fn=None,
              log=lambda m: None) -> list[dict]:
    """Bring the OPEN issues back in line with the witnesses. Returns the plan.

    Read-only unless `apply`. This is the rebuild path that makes "everything else is
    a cache" true rather than aspirational — without it the first fail-open miss
    strands a target outside the backlog with nothing to notice."""
    prs = claimed_issue_numbers(slug, run_fn=run_fn)
    if prs is None:
        log("could not list open PRs — refusing to reconcile "
            "(a failed lookup is not evidence that no PR exists)")
        return []
    staged = staged_issue_numbers(queue_dir)

    ok, out = _gh(["issue", "list", "--repo", slug, "--state", "open",
                   "--json", "number,labels", "--limit", "500"], run_fn=run_fn)
    if not ok:
        log(f"could not list issues ({out.strip()[:120]})")
        return []
    try:
        issues = json.loads(out.strip() or "[]")
    except ValueError:
        log("unparseable issue list")
        return []

    plan = reconcile_plan(issues, pr_numbers=prs, queue_ids=staged)
    if not plan:
        log(f"{len(issues)} open issues agree with {len(prs)} open PR(s) "
            f"and {len(staged)} staged stub(s)")
        return plan
    for row in plan:
        verb = "" if apply else "would "
        log(f"#{row['number']}: {verb}{row['now']} \u2192 {row['want']}")
        if apply:
            set_status(slug, row["number"], row["want"], run_fn=run_fn, log=log)
    return plan


def main(argv: list[str] | None = None) -> int:
    """Two modes:

        issue_state.py --repo O/N --issue 82 --status status:review
        issue_state.py --repo O/N --reconcile --queue targets/queue [--apply]

    The exit code is advisory. `open-pr.sh` runs the first form after the PR is
    already open, and a PR that exists is not made less real by a label that did not
    stick — so it discards the code and keeps going.
    """
    import argparse
    import sys

    err = lambda m: print(f"[issue-state] {m}", file=sys.stderr)   # noqa: E731

    ap = argparse.ArgumentParser(prog="issue_state")
    ap.add_argument("--repo", required=True, help="owner/name of the target library")
    ap.add_argument("--issue", type=int)
    ap.add_argument("--status", choices=list(STATUSES))
    ap.add_argument("--reconcile", action="store_true",
                    help="repair open issues against the open PRs + staged stubs")
    ap.add_argument("--queue", default="targets/queue",
                    help="queue dir holding cal-bk-N.lean stubs (--reconcile)")
    ap.add_argument("--apply", action="store_true",
                    help="write the reconcile plan; without it, report only")
    args = ap.parse_args(argv)

    if args.reconcile:
        if args.issue or args.status:
            ap.error("--reconcile takes the whole repo; drop --issue/--status")
        plan = reconcile(args.repo, args.queue, apply=args.apply, log=err)
        print(json.dumps({"repo": args.repo, "applied": bool(args.apply),
                          "plan": plan}, indent=2))
        return 0

    if not (args.issue and args.status):
        ap.error("--issue and --status are both required without --reconcile")
    return 0 if set_status(args.repo, args.issue, args.status, log=err) else 1


if __name__ == "__main__":
    raise SystemExit(main())
