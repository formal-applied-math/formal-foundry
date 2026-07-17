"""Bucket a run's summary rows into obstruction families.

Makes "why did the tick not produce a PR?" answerable at a glance instead of by
re-reading raw logs — the cheapest, highest-information thing to add given a
zero-PR streak (ML4TP harvest R5). Stdlib only; reads the vibe harness's
`runs/<tag>-summary.jsonl` rows (one per target).

v1 classifies on `outcome` (+ `gate_reason`). A finer `missing_premise` split of
`doesnt_compile` (unknown-identifier vs other errors) lands once the gate persists
its compile errors into the summary row.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys


def classify(row: dict) -> str:
    """Map a summary row to an obstruction family."""
    outcome = row.get("outcome", "")
    reason = row.get("gate_reason", "") or ""
    if outcome == "pass":
        return "solved"
    if outcome == "error":
        return "infra"
    if outcome == "max_rounds":
        return "prover_gave_up"
    if outcome == "budget_exhausted":
        return "budget_exhausted"
    if outcome == "fail_gate":
        if reason.startswith("forbidden"):
            return "used_forbidden_tactic"
        if reason == "axiom_dirty":
            return "axiom_dirty"
        if reason == "compile_or_sorry":
            return "doesnt_compile"
        return "fail_gate_other"
    return f"unknown:{outcome}"


FAMILY_HINT = {
    "solved": "candidate produced (expect runs/<tag>-<id>.lean)",
    "infra": "daemon/lean-lsp/vibe failure or no capture — retryable; check the flip + READY wait",
    "prover_gave_up": "hit max_turns with a sorry left — raise --max-turns, or the target is too big (decompose)",
    "used_forbidden_tactic": "banned tactic (native_decide/exact?/…) — strengthen the task's values gate",
    "axiom_dirty": "proof leans on a disallowed axiom — reject + reprompt",
    "doesnt_compile": "captured file has errors/sorry — likely missing premise or wrong API; improve pointers",
    "fail_gate_other": "gate rejected for another reason — inspect the row",
    "budget_exhausted": "ran out of budget mid-proof",
}


def triage(rows: list[dict]) -> collections.Counter:
    return collections.Counter(classify(r) for r in rows)


def load_summary(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return rows


def render(fams: collections.Counter, rows: list[dict]) -> str:
    lines = [f"triage of {len(rows)} target(s):"]
    if not rows:
        lines.append("  (no summary rows)")
    for fam, n in fams.most_common():
        lines.append(f"  {n:>3}  {fam:<22} — {FAMILY_HINT.get(fam, '')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="bucket a run summary into obstruction families")
    ap.add_argument("--summary", required=True, help="runs/<tag>-summary.jsonl")
    args = ap.parse_args()
    rows = load_summary(args.summary)
    print(render(triage(rows), rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
