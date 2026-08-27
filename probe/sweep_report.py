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

__all__ = ["rates", "refined_defects", "render_report", "load_records"]


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


def rates(records) -> dict:
    """Keyed by (arm, domain, status). `rate` is None when nothing was probed —
    distinct from 0.0, which means probed and nothing found."""
    reachable: dict[tuple, set] = collections.defaultdict(set)
    blind: dict[tuple, set] = collections.defaultdict(set)
    probed: collections.Counter = collections.Counter()
    certified: collections.Counter = collections.Counter()
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
        if r["verdict"] == "certified_unnecessary":
            certified[key] += 1
    out = {}
    for key in set(reachable) | set(blind) | set(probed):
        n, c = probed[key], certified[key]
        out[key] = {"probed": n, "certified": c,
                    "reachable_entries": len(reachable[key]),
                    "blind_entries": len(blind[key]),
                    "rate": (c / n) if n else None}
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
             "rate is not mistaken for a clean library.", "",
             "| arm | domain | status | reachable | blind | probed | certified | rate |",
             "|---|---|---|---|---|---|---|---|"]
    for (arm, domain, status), v in sorted(rate_table.items()):
        rate = "n/a" if v["rate"] is None else f"{100*v['rate']:.1f}%"
        lines.append(f"| {arm} | {domain} | {status} | {v['reachable_entries']} | "
                     f"{v['blind_entries']} | {v['probed']} | {v['certified']} | {rate} |")
    lines += ["", "## Defects human review caught that every gate passed", "",
              "| entry | issue | what review changed |", "|---|---|---|"]
    for d in defects:
        lines.append(f"| `{d['entry_id']}` | {d['issue']} | {d['refined']} |")
    return "\n".join(lines) + "\n"
