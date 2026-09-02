"""health — is the loop producing anything, and how loudly does it say so if not?

The pipeline ran unattended from 2026-07-28 to 2026-09-01 producing NOTHING: five
consecutive ticks, every one `max_rounds`, and every CI run green. The tick exits 0 on a
failed proof by design — a target the prover cannot close is not an infrastructure error
— so a green check means "the machinery ran", never "the machinery worked". Nobody
noticed for five weeks.

The distinction that matters for unattended operation is not pass-vs-fail on one tick but
whether the loop is STILL PRODUCING. One `max_rounds` is ordinary. Five in a row is a
foundry that has stopped working, and it should be impossible to miss.

Stdlib only; reads `pipeline_state.json`, which the tick already maintains.
"""
from __future__ import annotations

import datetime
import json

__all__ = ["assess", "render", "BARREN_THRESHOLD"]

#: consecutive non-pass ticks before the loop is declared stalled. At one tick per two
#: days this is under a week — long enough that a hard target or two does not cry wolf,
#: short enough that five weeks of silence cannot happen again.
BARREN_THRESHOLD = 3


def assess(state: dict, *, threshold: int = BARREN_THRESHOLD) -> dict:
    """Read a `pipeline_state.json` payload into a verdict about the loop itself."""
    history = list(state.get("history") or [])
    streak = 0
    for row in reversed(history):
        if row.get("outcome") == "pass":
            break
        streak += 1
    passes = [r for r in history if r.get("outcome") == "pass"]
    last = passes[-1] if passes else None
    return {"ticks": len(history),
            "passes": len(passes),
            "barren_streak": streak,
            "last_pass_id": (last or {}).get("id"),
            "last_pass_epoch": (last or {}).get("epoch"),
            "alarm": streak >= threshold,
            "threshold": threshold}


def render(h: dict) -> str:
    """A one-screen summary for a CI step, an issue body, or a terminal."""
    when = "never"
    if h.get("last_pass_epoch"):
        when = datetime.datetime.fromtimestamp(
            h["last_pass_epoch"], datetime.timezone.utc).date().isoformat()
    lines = [
        "## foundry health",
        "",
        f"- ticks recorded: **{h['ticks']}**, of which passed: **{h['passes']}**",
        f"- consecutive non-pass ticks: **{h['barren_streak']}** "
        f"(alarm at {h['threshold']})",
        f"- last pass: **{h.get('last_pass_id') or 'never'}** ({when})",
    ]
    if h["alarm"]:
        lines += ["",
                  f"**STALLED.** {h['barren_streak']} ticks in a row have produced no "
                  "candidate. The loop is running; it is not working. A green CI check "
                  "means the machinery ran, never that it worked."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="report whether the loop is still producing")
    ap.add_argument("--state", default="../pipeline_state.json")
    ap.add_argument("--threshold", type=int, default=BARREN_THRESHOLD)
    ap.add_argument("--fail-on-alarm", action="store_true",
                    help="exit 1 when the loop is stalled, so CI stops reporting success")
    args = ap.parse_args(argv)
    with open(args.state, encoding="utf-8") as f:
        h = assess(json.load(f), threshold=args.threshold)
    print(render(h))
    return 1 if (h["alarm"] and args.fail_on_alarm) else 0


if __name__ == "__main__":
    raise SystemExit(main())
