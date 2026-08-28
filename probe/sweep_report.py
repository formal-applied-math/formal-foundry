"""Aggregate necessity-sweep records into the tables the paper reports.

Two rules the numbers depend on, both enforced here rather than in the prose:
a binder counts only when its entry passed the power control, and a daemon error
counts nowhere. A rate over an unreachable population would be an artifact of the
instrument's blindness, which is exactly the misreading the paper has to prevent.
"""
from __future__ import annotations

import collections
import glob as _glob
import json

__all__ = ["rates", "refined_defects", "render_report", "load_records",
           "wilson"]


def load_records(path_glob: str) -> list[dict]:
    out = []
    for p in sorted(_glob.glob(path_glob)):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def wilson(k: int, n: int, z: float = 1.959963984540054):
    """Wilson score interval for `k` successes in `n` trials, or None when `n` is 0.

    The arm is a sample, not a census (`runs/necessity-sweep/daemon-stability.md`), so a
    bare point estimate would overstate what was measured. Wilson rather than the normal
    approximation because the rate is expected to be small and `n` modest — exactly where
    the normal interval runs off the end of [0, 1] and reports a negative lower bound for
    a proportion.

    One caveat the arithmetic cannot carry: binders drawn from the same theorem are not
    independent, and this interval assumes they are. The draw samples binders rather than
    entries to keep that clustering small, and `probed_entries` reports how many distinct
    theorems the probed binders came from so a reader can judge the residue.
    """
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (max(0.0, centre - half), min(1.0, centre + half))

def rates(records) -> dict:
    """Keyed by (arm, domain, status). `rate` is None when nothing was probed —
    distinct from 0.0, which means probed and nothing found."""
    reachable: dict[tuple, set] = collections.defaultdict(set)
    blind: dict[tuple, set] = collections.defaultdict(set)
    probed: collections.Counter = collections.Counter()
    certified: collections.Counter = collections.Counter()
    probed_entries: dict[tuple, set] = collections.defaultdict(set)
    for r in records:
        key = (r["arm"], r["domain"], r["status"])
        if r["verdict"] == "power_control":
            (reachable if r["sweep_proves_original"] else blind)[key].add(r["entry_id"])
            continue
        if r["verdict"] == "daemon_error" or not r["sweep_proves_original"]:
            continue
        if r["verdict"] == "free_filter_rejected":
            continue
        probed[key] += 1
        probed_entries[key].add(r["entry_id"])
        if r["verdict"] == "certified_unnecessary":
            certified[key] += 1
    out = {}
    for key in set(reachable) | set(blind) | set(probed):
        n, c = probed[key], certified[key]
        out[key] = {"probed": n, "certified": c,
                    "reachable_entries": len(reachable[key]),
                    "blind_entries": len(blind[key]),
                    "probed_entries": len(probed_entries[key]),
                    "rate": (c / n) if n else None,
                    "ci95": wilson(c, n)}
    return out


def refined_defects(bench_glob: str) -> list[dict]:
    """Entries whose provenance records what human review changed about the machine's
    statement — the labelled set of defects the automated gates passed."""
    out = []
    for p in sorted(_glob.glob(bench_glob)):
        with open(p, encoding="utf-8") as f:
            payload = json.load(f)
        for e in payload.get("theorems", []):
            prov = ((e.get("metadata") or {}).get("provenance")) or {}
            if prov.get("refined"):
                out.append({"entry_id": e.get("id", ""), "issue": prov.get("issue"),
                            "refined": prov["refined"]})
    return out


def render_report(rate_table: dict, defects: list[dict]) -> str:
    lines = ["# Necessity sweep — results", "",
             "`rate` = certified-unnecessary / probed, over entries the sweep can prove",
             "at all. `blind` entries are excluded from the rate and reported so a low",
             "rate is not mistaken for a clean library. The arm is a stratified sample,",
             "not a census, so every rate carries a Wilson interval; `entries probed` is",
             "how many distinct theorems the probed binders came from, since binders",
             "sharing a theorem are not independent trials.", "",
             "| arm | domain | status | reachable | blind | entries probed | probed | "
             "certified | rate | 95% CI |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (arm, domain, status), v in sorted(rate_table.items()):
        rate = "n/a" if v["rate"] is None else f"{100*v['rate']:.1f}%"
        ci = v.get("ci95")
        ci_s = "n/a" if ci is None else f"{100*ci[0]:.1f}–{100*ci[1]:.1f}%"
        lines.append(f"| {arm} | {domain} | {status} | {v['reachable_entries']} | "
                     f"{v['blind_entries']} | {v.get('probed_entries', 0)} | "
                     f"{v['probed']} | {v['certified']} | {rate} | {ci_s} |")
    lines += ["", "## Defects human review caught that every gate passed", "",
              "| entry | issue | what review changed |", "|---|---|---|"]
    for d in defects:
        lines.append(f"| `{d['entry_id']}` | {d['issue']} | {d['refined']} |")
    return "\n".join(lines) + "\n"
