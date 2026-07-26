"""Obstruction-family triage over the live queue — the standing feedback signal
(Task 1.7 of the autoformalization upgrade plan).

Every tick, aggregate the drafter's `refill-history.jsonl` and the prover's
`*-summary.jsonl` into the six failure families that name WHICH fix the pipeline
needs next. A change "helped" if its target family shrinks tick over tick. This
replaces a synthetic held-out bench: the signal is the real backlog.

Stdlib only; no Lean, no API, no network — pure aggregation over run artifacts.
"""

from __future__ import annotations

from collections import defaultdict

import provenance

# The six standing families: draft-side (unknown-id / depth / undraftable),
# prove-side (max-rounds), verdict (gate-fail), and infra (indeterminate).
FAMILIES = [
    "unknown-id-despite-retrieval",
    "depth-gate",
    "no-elaborating-draft",
    "prover-max-rounds",
    "gate-fail",
    "infra-indeterminate",
]


def _refill_family(row: dict) -> str | None:
    """Obstruction family for a `refill-history.jsonl` row (None if it seeded or is
    not an obstruction). Routing evidence takes precedence over trailing noise:
    a `depth`/`indeterminate` gate ANYWHERE in the history classifies the issue even
    when a later attempt died on a flaky intent parse."""
    out = row.get("outcome", "")
    if out == "seeded":
        return None
    gates = {h.get("gate") for h in row.get("history") or []}
    has_unknowns = any(h.get("unknown_identifiers") for h in row.get("history") or [])
    if out == "indeterminate" or "indeterminate" in gates:
        return "infra-indeterminate"
    if out in ("depth", "blocked_on_infra") or gates & {"depth", "blocked_on_infra"}:
        return "depth-gate"   # missing-primitives signal (feasibility census + depth gate)
    if has_unknowns:                     # a guessed constant survived retrieval
        return "unknown-id-despite-retrieval"
    if out in ("intent", "formalize") or gates & {"intent", "formalize"}:
        return "no-elaborating-draft"
    if out in ("unfaithful", "drift", "trivial", "vacuous", "false",
               "newdef_depth", "ungrounded") or \
            gates & {"unfaithful", "drift", "trivial", "vacuous", "false",
                     "newdef_depth", "ungrounded"}:
        return "gate-fail"
    return None


def _summary_family(row: dict) -> str | None:
    """Obstruction family for a prover `*-summary.jsonl` row (None on a pass)."""
    out = row.get("outcome", "")
    if out in ("max_rounds", "budget", "budget_exhausted"):
        return "prover-max-rounds"
    if out in ("fail_assembly",):
        return "gate-fail"
    return None


def bucket_obstructions(refill_rows: list[dict], summary_rows: list[dict]) -> dict:
    """`{family: {"count": int, "issues": {id: count}}}` over both sources — the
    tick-local obstruction census. `pass`/`seeded` rows contribute nothing."""
    buckets: dict[str, dict] = {f: {"count": 0, "issues": defaultdict(int)} for f in FAMILIES}
    for r in refill_rows:
        fam = _refill_family(r)
        if fam:
            buckets[fam]["count"] += 1
            buckets[fam]["issues"][str(r.get("issue"))] += 1
    for r in summary_rows:
        fam = _summary_family(r)
        if fam:
            buckets[fam]["count"] += 1
            buckets[fam]["issues"][str(r.get("target"))] += 1
    return {f: {"count": b["count"], "issues": dict(b["issues"])} for f, b in buckets.items()}


def load_rows(runs_dir: str) -> tuple[list[dict], list[dict]]:
    """The RAW `refill-history.jsonl` records + every `*-summary.jsonl` row, read through
    the provenance substrate (the single jsonl reader + file discovery). Record grain is
    preserved — the family classifier above needs each tick's whole history."""
    return (provenance.raw_refill_records(runs_dir),
            provenance.raw_summary_records(runs_dir))


def _trend(cur: int, prev: int | None) -> str:
    if prev is None:
        return ""
    d = cur - prev
    return "→" if d == 0 else (f"▲+{d}" if d > 0 else f"▼{d}")


def render_report(buckets: dict, *, prev: dict | None = None) -> str:
    """A markdown report — one row per family with count, a trend vs `prev` (the
    previous tick's buckets), and the top offending issues. The largest count is
    the fix the pipeline needs next."""
    total = sum(b["count"] for b in buckets.values())
    lines = [
        "# Obstruction families (live queue)",
        "",
        f"total obstructions: {total}",
        "",
        "| family | count | trend | top issues |",
        "|---|---|---|---|",
    ]
    for f in FAMILIES:
        c = buckets[f]["count"]
        prev_c = None if prev is None else prev.get(f, {}).get("count", 0)
        top = ", ".join(
            f"#{i}×{n}" if n > 1 else f"#{i}"
            for i, n in sorted(buckets[f]["issues"].items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"| {f} | {c} | {_trend(c, prev_c)} | {top} |")
    lines += ["", "_the family with the largest count names the fix the pipeline needs next._"]
    return "\n".join(lines) + "\n"
