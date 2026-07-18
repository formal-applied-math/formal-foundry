"""A/B scoreboard for the decomposition decision-gate (Task 2.6).

One row per real target-attempt per arm — `cron` (the plain vibe prove path) vs
`decompose` (the lemma-DAG loop), BOTH Mistral. It answers the single question the
2026-09-30 decision gate needs: does decomposition close hard targets the plain cron
path cannot, for the tokens it costs? There is NO centaur/Claude arm — production is
Mistral-only (R decision 2026-07-18). `refinery_minutes` is filled by hand at merge (the
unpriced human review cost). Stdlib only; the running log is runs/ab-decomposer.jsonl and
the human doc is docs/research/ab-decomposer.md.
"""

from __future__ import annotations

import json
import os

from probe_lib import append_jsonl

ARMS = ("cron", "decompose")
_START, _END = "<!-- SCOREBOARD:START -->", "<!-- SCOREBOARD:END -->"


def ab_row(*, target: str, arm: str, outcome: str, ts: str, leaves_total: int = 0,
           leaves_closed: int = 0, tokens: int = 0, refinery_minutes=None,
           note: str = "") -> dict:
    """One scoreboard row. `arm` must be `cron` or `decompose` (Mistral-only — a `claude`
    arm is rejected). `refinery_minutes` stays None until a human fills it at merge."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    return {"target": target, "arm": arm, "outcome": outcome, "ts": ts,
            "leaves_total": leaves_total, "leaves_closed": leaves_closed,
            "tokens": tokens, "refinery_minutes": refinery_minutes, "note": note}


def append_ab_row(runs_dir: str, row: dict) -> dict:
    """Append a scoreboard row to runs/ab-decomposer.jsonl (the machine log render reads)."""
    append_jsonl(os.path.join(runs_dir, "ab-decomposer.jsonl"), row)
    return row


def _leaves_cell(r: dict) -> str:
    return (f"{r.get('leaves_closed', 0)}/{r.get('leaves_total', 0)}"
            if r.get("arm") == "decompose" else "—")


def render_scoreboard(rows) -> str:
    """A markdown table (newest first) of the ab rows — leaves shown only for the
    decompose arm; a blank refinery cell = not yet reviewed/merged."""
    head = ("| ts | target | arm | outcome | leaves | tokens | refinery min |\n"
            "|----|--------|-----|---------|--------|--------|--------------|")
    lines = [
        f"| {r.get('ts', '')} | {r.get('target', '')} | {r.get('arm', '')} | "
        f"{r.get('outcome', '')} | {_leaves_cell(r)} | {r.get('tokens', 0)} | "
        f"{'' if r.get('refinery_minutes') is None else r['refinery_minutes']} |"
        for r in reversed(rows)
    ]
    return head + "\n" + ("\n".join(lines) if lines else "| (no rows yet) |") + "\n"


def update_scoreboard_md(md_path: str, runs_dir: str) -> None:
    """Rewrite the table between the SCOREBOARD markers in `md_path` from the jsonl log —
    the per-tick refresh. No-op (leaves the doc untouched) if the markers are absent."""
    try:
        rows = [json.loads(line) for line in open(os.path.join(runs_dir, "ab-decomposer.jsonl"))
                if line.strip()]
    except OSError:
        rows = []
    text = open(md_path, encoding="utf-8").read()
    if _START not in text or _END not in text:
        return
    pre, rest = text.split(_START, 1)
    _, post = rest.split(_END, 1)
    new = f"{pre}{_START}\n{render_scoreboard(rows)}{_END}{post}"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(new)
