"""Draft-elaboration eval: measure how often the drafter produces an ELABORATING statement,
draft-only (no prove), against the warm local daemon. A/B the two-stage drafter (magistral
intent -> leanstral formalize) vs the legacy single-stage magistral drafter, with/without
retrieval, so each lever's delta on the elaboration rate is visible in minutes, not 45-min
CI rolls. Design: docs/superpowers/specs/2026-07-14-leanstral-drafter-two-stage-design.md.

Usage (daemon up):
  python3 eval_draft.py --main-repo /path/to/formal-mathfin --issues 53,61,67
  python3 eval_draft.py --main-repo /path/to/formal-mathfin --mode baseline --limit 8
  python3 eval_draft.py --main-repo /path/to/formal-mathfin --issues 67 --no-retrieval

`summarize`/`format_table` are pure (unit-tested); the rest drives the models + daemon.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from house_context import extract_signatures
from pipeline_lib import AutoformalizeConfig
from probe import daemon_check, mistral_chat

import autoformalize as af


def summarize(results: list[dict]) -> dict:
    """Aggregate the per-issue rows into rates (elaboration = drafter produced an elaborating
    stub; depth = that stub also passes the pointers-scoped depth gate)."""
    n = len(results)
    elaborated = sum(1 for r in results if r["elaborated"])
    deep = sum(1 for r in results if r.get("depth_pass"))
    return {"n": n, "elaborated": elaborated, "deep": deep,
            "elaboration_rate": round(elaborated / n, 3) if n else 0.0,
            "depth_rate": round(deep / n, 3) if n else 0.0,
            "tokens": sum(r["tokens"] for r in results)}


def format_table(results: list[dict]) -> str:
    rows = [f"{'issue':>6}  {'elab':>4}  {'depth':>5}  {'tokens':>7}  {'wall_s':>7}"]
    for r in results:
        rows.append(f"{r['issue']:>6}  {('Y' if r['elaborated'] else 'n'):>4}  "
                    f"{('Y' if r.get('depth_pass') else '-'):>5}  "
                    f"{r['tokens']:>7}  {r['wall_s']:>7.1f}")
    return "\n".join(rows)


def eval_issue(issue: dict, *, mode: str, intent_fn, formalize_fn, check_fn, main_repo: str,
               formalize_rounds: int, retrieve_fn, pins: str = "") -> dict:
    """Draft ONLY (no prove) one issue; record whether it elaborates and passes the depth gate."""
    ctx = extract_signatures(main_repo, issue.get("pointers", [])) if issue.get("pointers") else ""
    t0 = time.time()
    tokens, ok, lean_text, name = 0, False, None, None
    if mode == "baseline":
        dr = af.draft_with_repair(issue, ctx, pins, chat_fn=intent_fn, check_fn=check_fn,
                                  emit_fn=af.emit_target_files, rounds=formalize_rounds)
        tokens, ok = dr["tokens"], dr["ok"]
        if ok:
            lean_text, name = dr["lean_text"], af.split_statement(dr["stub"])[0]
    else:  # two-stage
        di = af.draft_intent(issue, ctx, chat_fn=intent_fn)
        tokens += di["tokens"]
        if di["ok"]:
            fr = af.formalize_with_repair(di["intent"], ctx, issue=issue, chat_fn=formalize_fn,
                                          check_fn=check_fn, emit_fn=af.emit_target_files,
                                          rounds=formalize_rounds, retrieve_fn=retrieve_fn)
            tokens += fr["tokens"]
            ok = fr["ok"]
            if ok:
                lean_text, name = fr["lean_text"], af.split_statement(fr["stub"])[0]
    depth_pass = None
    if ok:
        depth_pass = not af.depth_rejection(lean_text, name, issue.get("pointers", []),
                                            check_fn=check_fn)["shallow"]
    return {"issue": issue["number"], "elaborated": ok, "depth_pass": depth_pass,
            "tokens": tokens, "wall_s": time.time() - t0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main-repo", required=True)
    ap.add_argument("--issues", default=None, help="comma-separated issue numbers (default: first --limit ready)")
    ap.add_argument("--slug", default="raphaelrrcoelho/formal-mathfin")
    ap.add_argument("--mode", choices=["two-stage", "baseline"], default="two-stage")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-retrieval", dest="retrieval", action="store_false")
    ap.add_argument("--formalize-rounds", type=int, default=None)
    args = ap.parse_args()

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AutoformalizeConfig.load(args.config or os.path.join(root, "pipeline.toml"))
    formalize_rounds = args.formalize_rounds or cfg.formalize_rounds

    issues = af.prepare_issues(af._fetch_issues(args.slug))
    if args.issues:
        want = {int(x) for x in args.issues.split(",")}
        issues = [i for i in issues if i["number"] in want]
    else:
        issues = issues[:args.limit]
    if not issues:
        print("no ready issues to eval", file=sys.stderr)
        return 1

    def intent_fn(msgs):
        return mistral_chat(msgs, api_key=api_key, model=cfg.intent_model, max_tokens=cfg.draft_max_tokens)

    def formalize_fn(msgs):
        return mistral_chat(msgs, api_key=api_key, model=cfg.formalize_model, reasoning_effort="high")

    retrieve_fn = (lambda nm: af.loogle_candidates(nm, main_repo=args.main_repo)) if args.retrieval else None

    results = []
    for issue in issues:
        r = eval_issue(issue, mode=args.mode, intent_fn=intent_fn, formalize_fn=formalize_fn,
                       check_fn=daemon_check, main_repo=args.main_repo,
                       formalize_rounds=formalize_rounds, retrieve_fn=retrieve_fn)
        results.append(r)
        print(f"[eval] #{r['issue']}: elaborated={r['elaborated']} depth={r['depth_pass']} "
              f"tokens={r['tokens']} wall={r['wall_s']:.1f}s", file=sys.stderr)
    print(format_table(results))
    print(json.dumps({"mode": args.mode, "retrieval": args.retrieval, **summarize(results)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
