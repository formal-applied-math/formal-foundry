"""A/B scoreboard for the decomposition decision-gate (Task 2.6).

One row per real target-attempt per arm — `cron` (the plain prove path) vs `decompose`
(the lemma-DAG loop). It answers one question: does decomposition close hard targets the
plain path cannot, for the tokens it costs? `refinery_minutes` is filled by hand at merge
(the unpriced human review cost). Stdlib only; the running log is runs/ab-decomposer.jsonl and
the human doc is docs/research/ab-decomposer.md.
"""

from __future__ import annotations

import os

import provenance
from probe_lib import append_jsonl

#: which PATH ran — direct prove vs the lemma-DAG loop.
ARMS = ("cron", "decompose")

#: which ENGINE proved — a separate question from the path, so a row says both. These are
#: the engines a NEW row may name (`pipeline_lib.PROVER_LAUNCHERS`); rows on disk keep
#: whatever engine they recorded when they were written.
ENGINES = ("claude",)
DEFAULT_ENGINE = "claude"
_START, _END = "<!-- SCOREBOARD:START -->", "<!-- SCOREBOARD:END -->"


def ab_row(*, target: str, arm: str, outcome: str, ts: str, leaves_total: int = 0,
           leaves_closed: int = 0, tokens: int = 0, refinery_minutes=None,
           note: str = "", engine: str = DEFAULT_ENGINE) -> dict:
    """One scoreboard row. `arm` is the path (`cron`/`decompose`), `engine` the prover. `refinery_minutes` stays None until a human fills it at
    merge. Both fields are guarded, so a typo cannot quietly open a new column in the
    A/B."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
    return {"target": target, "arm": arm, "outcome": outcome, "ts": ts,
            "leaves_total": leaves_total, "leaves_closed": leaves_closed,
            "tokens": tokens, "refinery_minutes": refinery_minutes, "note": note,
            "engine": engine}


def append_ab_row(runs_dir: str, row: dict) -> dict:
    """Append a scoreboard row to runs/ab-decomposer.jsonl (the machine log render reads)."""
    append_jsonl(os.path.join(runs_dir, "ab-decomposer.jsonl"), row)
    return row


def _leaves_cell(r: dict) -> str:
    return (f"{r.get('leaves_closed', 0)}/{r.get('leaves_total', 0)}"
            if r.get("arm") == "decompose" else "—")


#: notes are prose and can run long; the full text stays in the jsonl
_NOTE_WIDTH = 120


def _note_cell(r: dict) -> str:
    """The row's note, pipe-escaped and clipped. An outcome without its reason is a
    verdict a reader cannot check, and this column is also where a hand correction goes
    — the same convention `refinery_minutes` already follows."""
    note = str(r.get("note") or "").replace("|", "\\|").replace("\n", " ").strip()
    return note if len(note) <= _NOTE_WIDTH else note[:_NOTE_WIDTH - 1].rstrip() + "…"

def render_scoreboard(rows) -> str:
    """A markdown table (newest first) of the ab rows — leaves shown only for the
    decompose arm; a blank refinery cell = not yet reviewed/merged."""
    head = ("| ts | target | arm | engine | outcome | leaves | tokens | refinery min "
            "| note |\n"
            "|----|--------|-----|--------|---------|--------|--------|--------------"
            "|------|")
    lines = [
        f"| {r.get('ts', '')} | {r.get('target', '')} | {r.get('arm', '')} | "
        f"{r.get('engine') or '—'} | "
        f"{r.get('outcome', '')} | {_leaves_cell(r)} | {r.get('tokens', 0)} | "
        f"{'' if r.get('refinery_minutes') is None else r['refinery_minutes']} | "
        f"{_note_cell(r)} |"
        for r in reversed(rows)
    ]
    return head + "\n" + ("\n".join(lines) if lines else "| (no rows yet) |") + "\n"


def update_scoreboard_md(md_path: str, runs_dir: str) -> None:
    """Rewrite the table between the SCOREBOARD markers in `md_path` from the jsonl log —
    the per-tick refresh. No-op (leaves the doc untouched) if the markers are absent."""
    rows = provenance.read_jsonl(os.path.join(runs_dir, "ab-decomposer.jsonl"))
    text = open(md_path, encoding="utf-8").read()
    if _START not in text or _END not in text:
        return
    pre, rest = text.split(_START, 1)
    _, post = rest.split(_END, 1)
    new = f"{pre}{_START}\n{render_scoreboard(rows)}{_END}{post}"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(new)
