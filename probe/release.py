"""release — what happens to a target after the library refuses to assemble its proof.

The proof passed the foundry's gate; the library's build (`domains/<name>/regen.sh`, a
copy of its CI) refused the module: a ratchet rose, a values gate tripped. Until
2026-11-12 the tick recorded `fail_assembly` and stopped there. The stub stayed staged,
its id stayed in `attempted_issues`, and the issue stayed `status:in-progress` with
nothing left that would ever touch it. #318 was released by hand three times in one day.

Now the refusal is a lesson, and the target gets a bounded number of further tries, each
told why the last one was refused:

1. the first refusal re-proves the SAME statement: the reason goes into the prover's
   notebook (`experience.json`), and the refused proof is marked so it is never reused;
2. the second retires the stub and releases the issue for a REDRAFT: the reason goes into
   the drafter's lesson (`refill-history.jsonl`), and the issue returns to `status:ready`
   at the next reconcile;
3. from the third on, the redraft is ordered behind every other issue (a demoted family),
   so one issue the library keeps refusing cannot hold the pipeline.

The count is read from `pipeline_state.json`'s `voided` rows, which carry the reason of
every release, so it survives across ticks and is visible to a person reading the state.

    python3 release.py --state pipeline_state.json --queue targets/queue \\
        --runs runs --id cal-bk-318 --tag pipeline-… --config pipeline.toml

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

#: refill-history outcomes a release writes; `af_routing.classify_refill` maps them
REDRAFT, PARKED = "assembly", "assembly_parked"
RELEASE_MARK = "released after the library refused the assembly"


def reason_of(runs_dir: str, tag: str, target_id: str) -> str:
    """One line: what the library refused, from open-pr's `<tag>-<id>.blocked.json`."""
    try:
        with open(os.path.join(runs_dir, f"{tag}-{target_id}.blocked.json"),
                  encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, ValueError):
        return "the library's build refused the module (no reason recorded)"
    parts = [str(b.get("reason") or "").strip()]
    if b.get("ratchets"):
        parts.append("ratchets that rose: " + "; ".join(b["ratchets"]))
    return " — ".join(p for p in parts if p)


def prior_releases(state: dict, target_id: str) -> int:
    return sum(1 for v in state.get("voided", [])
               if v.get("id") == target_id and RELEASE_MARK in str(v.get("voided") or ""))


def issue_of(target_id: str) -> int | None:
    m = re.fullmatch(r"cal-bk-(\d+)", target_id)
    return int(m.group(1)) if m else None


def release(state: dict, target_id: str, reason: str) -> dict:
    """Decide and apply the state half of a release (pure on `state`). Returns
    `{action, n}`: `reprove` the same stub, `redraft` the issue, or `park` it (a redraft
    ordered last). The latest `fail_assembly` history row moves to `voided` with the
    reason, and the id leaves `attempted_issues`, so the planner will select the target
    (or its redraft, which keeps the id) again."""
    n = prior_releases(state, target_id)
    action = "reprove" if n == 0 else ("redraft" if n == 1 else "park")
    rows = [h for h in state.get("history", [])
            if h.get("id") == target_id and h.get("outcome") == "fail_assembly"]
    if rows:
        row = rows[-1]
        row["voided"] = (f"{RELEASE_MARK} ({n + 1}, {action}, "
                         f"{time.strftime('%Y-%m-%d')}): {reason}")[:900]
        state["history"] = [h for h in state["history"] if h is not row]
        state.setdefault("voided", []).append(row)
    state["attempted_issues"] = [x for x in state.get("attempted_issues", [])
                                 if x != target_id]
    return {"action": action, "n": n + 1}


def refused_marker(runs_dir: str, tag: str, target_id: str) -> str:
    """The file that tells `prove_session.adopt_verified` never to reuse this proof."""
    return os.path.join(runs_dir, f"{tag}-{target_id}.refused")


def apply(*, state_path: str, queue_dir: str, runs_dir: str, target_id: str, tag: str,
          experience=None, routing_arch: str = "", log=lambda m: None) -> dict:
    """The whole release: state, the refused proof, the lesson, and the stub."""
    reason = reason_of(runs_dir, tag, target_id)
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    out = release(state, target_id, reason)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    with open(refused_marker(runs_dir, tag, target_id), "w", encoding="utf-8") as f:
        f.write(reason + "\n")
    issue = issue_of(target_id)
    if out["action"] == "reprove":
        if experience is not None:
            experience.record(target_id, {
                "outcome": "fail_assembly",
                "reason": "the proof passed the gate and the library refused the module: "
                          + reason,
                "errors": [reason]})
    else:
        from issue_state import retire_stubs
        retire_stubs(queue_dir, [target_id])
        if experience is not None:
            experience.forget(target_id)       # a redraft is another statement
        if issue is not None:
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "issue": issue, "attempts": 0,
                   "outcome": REDRAFT if out["action"] == "redraft" else PARKED,
                   "history": [{"gate": "assembly",
                                "detail": ("a proof of the last draft passed and the "
                                           "library refused its module: " + reason)[:400]}],
                   "arch": routing_arch}
            with open(os.path.join(runs_dir, "refill-history.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log(f"{target_id}: {out['action']} (release {out['n']}): {reason[:200]}")
    return {**out, "reason": reason}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release")
    ap.add_argument("--state", required=True)
    ap.add_argument("--queue", required=True, help="the queue directory")
    ap.add_argument("--runs", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    from af_routing import ROUTING_ARCH
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from prove_session import _experience_store
        store = _experience_store(args.runs, args.config)
    except Exception:                       # noqa: BLE001 — a lesson is never worth a crash
        store = None
    r = apply(state_path=args.state, queue_dir=args.queue, runs_dir=args.runs,
              target_id=args.id, tag=args.tag, experience=store,
              routing_arch=ROUTING_ARCH,
              log=lambda m: print(f"[release] {m}", file=sys.stderr))
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
