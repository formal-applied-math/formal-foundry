"""health — is the loop producing anything, and how loudly does it say so if not?

The pipeline ran unattended from 2026-07-28 to 2026-09-01 producing NOTHING: five
consecutive ticks, every one `max_rounds`, and every CI run green. The tick exits 0 on a
failed proof by design — a target the prover cannot close is not an infrastructure error
— so a green check means "the machinery ran", never "the machinery worked". Nobody
noticed for five weeks.

The distinction that matters for unattended operation is not pass-vs-fail on one tick but
whether the loop is STILL PRODUCING. One `max_rounds` is ordinary. Five in a row is a
foundry that has stopped working, and it should be impossible to miss.

Stdlib only; reads `pipeline_state.json`, which the tick already maintains, and
`runs/ticks.jsonl`, one row per tick whatever happened.

The history check alone was blind to the failure that actually happened. It counts
RECORDED outcomes, and the ticks that did nothing — a manifest blocked by one bad stub, a
prover launcher that crashed before starting — either recorded nothing or recorded a crash
as `max_rounds`. The second check reads every tick and asks the question that matters
unattended: did a prover actually run to a verdict?
"""
from __future__ import annotations

import datetime
import json

__all__ = ["assess", "assess_ticks", "render", "record_tick", "BARREN_THRESHOLD",
           "IDLE_THRESHOLD"]

#: consecutive non-pass ticks before the loop is declared stalled. At one tick per two
#: days this is under a week — long enough that a hard target or two does not cry wolf,
#: short enough that five weeks of silence cannot happen again.
BARREN_THRESHOLD = 3

#: consecutive ticks in which no prover ran to a verdict (a skip other than `not_due`, a
#: crash, a failed canary) before the loop is declared idle. Two: one skip can be a quiet
#: queue; two in a row is a machine that is not doing its job, whatever the reason.
IDLE_THRESHOLD = 2

#: outcomes that mean a prover session ran and the tick reached a verdict about a target
_VERDICTS = ("pass", "max_rounds", "fail_gate", "fail_assembly")


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


def _productive(row: dict) -> bool:
    return (row.get("action") == "run" and not row.get("infra_failure")
            and row.get("outcome") in _VERDICTS)


def assess_ticks(rows: list[dict], *, threshold: int = IDLE_THRESHOLD) -> dict:
    """From `runs/ticks.jsonl`: how many of the most recent ticks did no real work.
    `not_due` skips are the cadence working as designed and neither extend nor break the
    streak."""
    streak, reasons = 0, []
    for row in reversed(rows):
        if row.get("action") == "skip" and row.get("reason") == "not_due":
            continue
        if _productive(row):
            break
        streak += 1
        reasons.append(row.get("infra_reason") or row.get("reason")
                       or row.get("outcome") or row.get("action") or "?")
    return {"idle_streak": streak, "idle_reasons": reasons[:5],
            "idle_alarm": streak >= threshold, "idle_threshold": threshold}


def load_ticks(path: str | None) -> list[dict]:
    """The tick log, oldest first; [] when absent (older runs never wrote one)."""
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except (OSError, TypeError):
        pass
    return rows


def record_tick(path: str, **fields) -> dict:
    """Append one tick row. Called from the tick's EXIT trap, so it must never raise on
    odd input: every field is optional and stringly-typed from the shell."""
    import time
    infra = str(fields.get("infra_failure") or "").strip() in ("1", "true", "True")
    try:
        code = int(fields.get("exit_code") or 0)
    except ValueError:
        code = -1
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tag": fields.get("tag") or None,
           "action": fields.get("action") or "aborted",
           "reason": fields.get("reason") or None, "target": fields.get("target") or None,
           "outcome": fields.get("outcome") or None, "engine": fields.get("engine") or None,
           "infra_failure": infra, "infra_reason": fields.get("infra_reason") or None,
           "exit_code": code}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


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
    if "idle_streak" in h:
        lines.append(f"- consecutive ticks with no prover verdict: **{h['idle_streak']}** "
                     f"(alarm at {h['idle_threshold']})")
    if h["alarm"]:
        lines += ["",
                  f"**STALLED.** {h['barren_streak']} ticks in a row have produced no "
                  "candidate. The loop is running; it is not working. A green CI check "
                  "means the machinery ran, never that it worked."]
    if h.get("idle_alarm"):
        lines += ["",
                  f"**IDLE.** {h['idle_streak']} ticks in a row ran no prover to a verdict. "
                  "Most recent reasons: " + "; ".join(str(r) for r in h["idle_reasons"])]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    import argparse
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["record-tick"]:
        rt = argparse.ArgumentParser(description="append one row to runs/ticks.jsonl")
        rt.add_argument("--ticks", required=True)
        for key in ("tag", "action", "reason", "target", "outcome", "engine",
                    "infra-failure", "infra-reason", "exit-code"):
            rt.add_argument(f"--{key}", default="")
        a = rt.parse_args(argv[1:])
        record_tick(a.ticks, tag=a.tag, action=a.action, reason=a.reason, target=a.target,
                    outcome=a.outcome, engine=a.engine, infra_failure=a.infra_failure,
                    infra_reason=a.infra_reason, exit_code=a.exit_code)
        return 0
    ap = argparse.ArgumentParser(description="report whether the loop is still producing")
    ap.add_argument("--state", default="../pipeline_state.json")
    ap.add_argument("--ticks", default=None, help="runs/ticks.jsonl (every tick, not just recorded ones)")
    ap.add_argument("--threshold", type=int, default=BARREN_THRESHOLD)
    ap.add_argument("--idle-threshold", type=int, default=IDLE_THRESHOLD)
    ap.add_argument("--fail-on-alarm", action="store_true",
                    help="exit 1 when the loop is stalled or idle, so CI stops reporting success")
    args = ap.parse_args(argv)
    with open(args.state, encoding="utf-8") as f:
        h = assess(json.load(f), threshold=args.threshold)
    if args.ticks:
        h.update(assess_ticks(load_ticks(args.ticks), threshold=args.idle_threshold))
    print(render(h))
    alarm = h["alarm"] or h.get("idle_alarm", False)
    return 1 if (alarm and args.fail_on_alarm) else 0


if __name__ == "__main__":
    raise SystemExit(main())
