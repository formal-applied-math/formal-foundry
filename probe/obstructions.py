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
    # An outcome none of the above recognises. It used to classify as None and be
    # DROPPED, which made the census silently incomplete: 8 of 42 refill records
    # were `error`, 7 of them issue #71 failing the same way every tick, and not
    # one appeared in a report. This footer promises "the family with the largest
    # count names the fix the pipeline needs next" — a family that cannot be
    # counted can never be largest.
    "drafter-error",
]


_GATE_FAIL = {"unfaithful", "drift", "trivial", "vacuous", "false",
              "newdef_depth", "ungrounded"}


def _family_from_signals(gates: set, has_unknowns: bool) -> str | None:
    """The draft obstruction family from a tick's gate SET + whether any attempt guessed
    an unknown id. Routing evidence takes precedence over trailing noise: a
    `depth`/`indeterminate` gate ANYWHERE outranks a later flaky intent parse. Shared by
    the raw record classifier and the substrate census so they can never diverge; the
    caller filters out seeded ticks before calling."""
    if "indeterminate" in gates:
        return "infra-indeterminate"
    if gates & {"depth", "blocked_on_infra"}:
        return "depth-gate"   # missing-primitives signal (feasibility census + depth gate)
    if has_unknowns:                     # a guessed constant survived retrieval
        return "unknown-id-despite-retrieval"
    if gates & {"intent", "formalize"}:
        return "no-elaborating-draft"
    if gates & _GATE_FAIL:
        return "gate-fail"
    # NOT None. Both callers filter `seeded` before reaching here, so anything left
    # is a real obstruction whose outcome this function does not recognise — an
    # exception in the draft/emit path, most often. Returning None dropped it.
    return "drafter-error"


def _refill_family(row: dict) -> str | None:
    """Obstruction family for a whole `refill-history.jsonl` record (None if it seeded or
    is not an obstruction). Folds the record outcome into the gate set — for a non-seeded
    record the outcome IS the last gate, so this preserves the old `out in (...)` clauses."""
    if row.get("outcome", "") == "seeded":
        return None
    gates = {h.get("gate") for h in row.get("history") or []}
    gates.add(row.get("outcome"))
    has_unknowns = any(h.get("unknown_identifiers") for h in row.get("history") or [])
    return _family_from_signals(gates, has_unknowns)


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


def census(runs_dir: str) -> dict:
    """The obstruction census computed off the NORMALIZED provenance substrate rather than
    re-parsing the raw jsonl — the deep wiring. Draft records are regrouped per tick
    `(issue, ts)` (the record grain the family precedence needs) and classified by the
    SAME `_family_from_signals` the raw path uses; prove records from the `*-summary`
    stream feed the prover-side families. The A/B decomposer arm (also in the substrate)
    is excluded, matching the historical census. Byte-identical to
    `bucket_obstructions(*load_rows(runs_dir))` — enforced by test."""
    records = provenance.iter_run_records(runs_dir)
    buckets: dict[str, dict] = {f: {"count": 0, "issues": defaultdict(int)} for f in FAMILIES}

    ticks: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        if r["stage"] == "draft":
            ticks[(r["issue"], r["ts"])].append(r)
    for (issue, _ts), group in ticks.items():
        if any(g.get("tick_outcome") == "seeded" for g in group):
            continue                                    # a seed (even after failures) never counts
        gates = {g.get("gate") for g in group} | {g.get("tick_outcome") for g in group}
        has_unknowns = any(g.get("unknowns") for g in group)
        fam = _family_from_signals(gates, has_unknowns)
        if fam:
            buckets[fam]["count"] += 1
            buckets[fam]["issues"][str(issue)] += 1

    for r in records:
        # the *-summary prove stream only — not the A/B decomposer arm log
        if r["stage"] != "prove" or r.get("source_file") == "ab-decomposer.jsonl":
            continue
        fam = _summary_family({"outcome": r.get("outcome", "")})
        if fam:
            buckets[fam]["count"] += 1
            buckets[fam]["issues"][str(r.get("target"))] += 1

    return {f: {"count": b["count"], "issues": dict(b["issues"])} for f, b in buckets.items()}


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
