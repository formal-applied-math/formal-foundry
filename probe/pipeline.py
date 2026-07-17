"""The scheduled tick's decision brain (CLI over pipeline_lib).

`pipeline-tick.sh` calls this twice:
  1. `plan`   — decide whether to run and on which target; prints a JSON decision.
  2. `record` — after the prover runs, charge the budget + persist state.

Splitting decision (here) from action (the shell) keeps the pacing logic pure
and unit-testable while the shell owns the Lean/prover/gh side effects.

  python3 pipeline.py plan   --config ../pipeline.toml --state ../pipeline_state.json \
                             --queue ../targets/queue/manifest.json
  python3 pipeline.py record --config ../pipeline.toml --state ../pipeline_state.json \
                             --id cal-bk-1 --difficulty difficulty:small --outcome pass
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import pipeline_lib as P
from pipeline_lib import PipelineConfig


def _now(args):
    now_epoch = args.now_epoch if args.now_epoch is not None else int(time.time())
    now_ym = args.now_ym or time.strftime("%Y-%m", time.gmtime(now_epoch))
    return now_epoch, now_ym


def cmd_plan(args) -> int:
    cfg = PipelineConfig.load(args.config)
    state = P.load_state(args.state)
    now_epoch, now_ym = _now(args)
    state = P.roll_month(state, now_ym)

    def emit(decision: dict) -> int:
        print(json.dumps(decision))
        return 0

    if not args.force and not P.due(state, cfg, now_epoch):
        return emit({"action": "skip", "reason": "not_due"})

    try:
        queue = json.load(open(args.queue))
        candidates = queue.get("targets", []) if isinstance(queue, dict) else list(queue)
    except (OSError, ValueError):
        candidates = []
    target = P.next_target(candidates, state)
    if target is None:
        return emit({"action": "skip", "reason": "no_unattempted_targets"})

    difficulty = target.get("difficulty")
    if not P.can_afford(state, cfg, difficulty):
        return emit({"action": "skip", "reason": "budget_exhausted",
                     "remaining": P.budget_remaining(state, cfg)})

    return emit({"action": "run", "target": target,
                 "budget": P.issue_budget(cfg, difficulty),
                 "reasoning_effort": cfg.reasoning_effort,
                 "max_turns": cfg.max_turns,
                 "remaining": P.budget_remaining(state, cfg)})


def cmd_record(args) -> int:
    cfg = PipelineConfig.load(args.config)
    state = P.load_state(args.state)
    now_epoch, now_ym = _now(args)
    # conservative accounting: charge the per-issue cap unless exact tokens given
    tokens = args.tokens if args.tokens is not None else P.issue_budget(cfg, args.difficulty)
    state = P.record_attempt(state, args.id, tokens=tokens, outcome=args.outcome,
                             now_epoch=now_epoch, now_ym=now_ym)
    P.save_state(args.state, state)
    print(json.dumps({"recorded": args.id, "charged": tokens,
                      "spent_this_month": state["tokens_spent_this_month"],
                      "remaining": P.budget_remaining(state, cfg)}))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now-epoch", type=int, default=None, help="override clock (testing)")
    ap.add_argument("--now-ym", default=None, help="override YYYY-MM (testing)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--config", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--queue", required=True)
    p.add_argument("--force", action="store_true", help="ignore the due check")
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("record")
    r.add_argument("--config", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--id", required=True)
    r.add_argument("--difficulty", default=None)
    r.add_argument("--tokens", type=int, default=None)
    r.add_argument("--outcome", default="unknown")
    r.set_defaults(fn=cmd_record)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
