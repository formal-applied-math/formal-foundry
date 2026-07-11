"""Turn a proven candidate into edits on a formal-mathfin checkout: the real
proof in a MathFin module + a re-export benchmark entry. Pure file ops (no git,
no network) so it is testable on a temp tree. `scripts/open-pr.sh` owns
branch/commit/PR.

Benchmark JSON is appended with a minimal-diff round-trip: the corpus is
indent=2 / ensure_ascii=False, so re-dumping keeps existing entries byte-stable;
we only add the trailing comma + the new object and preserve the file's own
trailing-newline convention (some files have it, some do not).
"""

from __future__ import annotations

import json
import os
import re


def _module_name(main_module: str) -> str:
    """'MathFin/FX/Siegel.lean' -> 'MathFin.FX.Siegel'."""
    p = main_module[:-5] if main_module.endswith(".lean") else main_module
    return p.replace("/", ".")


def ensure_umbrella_import(main_repo: str, main_module: str,
                           umbrella: str = "MathFin.lean") -> list[str]:
    """Append `import <Module>` to the `MathFin.lean` umbrella so `lake build`
    puts the new module in the build graph (a benchmark snippet importing an
    unbuilt module gets a silently-empty environment — the umbrella header even
    warns about this). No-op if already present. Returns [umbrella] if it
    changed, else []."""
    mod_name = _module_name(main_module)
    umbrella_abs = os.path.join(main_repo, umbrella)
    text = open(umbrella_abs, encoding="utf-8").read()
    if re.search(rf"^import {re.escape(mod_name)}\s*$", text, re.MULTILINE):
        return []
    sep = "" if text.endswith("\n") or not text else "\n"
    with open(umbrella_abs, "w", encoding="utf-8") as f:
        f.write(text + sep + f"import {mod_name}\n")
    return [umbrella]


def append_entry(benchmark_text: str, entry: dict) -> str:
    """Append `entry` to a benchmark file's theorem list, preserving the file's
    indent=2 / ensure_ascii=False formatting and its trailing-newline choice."""
    had_newline = benchmark_text.endswith("\n")
    data = json.loads(benchmark_text)
    if isinstance(data, dict) and "theorems" in data:
        data["theorems"].append(entry)
    elif isinstance(data, list):
        data.append(entry)
    else:  # dict without a theorems list — treat the doc itself as the container
        raise ValueError("benchmark file has no 'theorems' list and is not a bare list")
    out = json.dumps(data, indent=2, ensure_ascii=False)
    return out + "\n" if had_newline else out


def apply_contribution(candidate_code: str, target: dict, benchmark_entry: dict,
                       main_repo: str) -> list[str]:
    """Write the proven candidate to `target['main_module']` and append
    `benchmark_entry` to `target['benchmark']`, both under `main_repo`. Returns
    the repo-relative paths written (for logging / `git add`)."""
    written: list[str] = []

    mod_rel = target["main_module"]
    mod_abs = os.path.join(main_repo, mod_rel)
    os.makedirs(os.path.dirname(mod_abs), exist_ok=True)
    with open(mod_abs, "w", encoding="utf-8") as f:
        f.write(candidate_code if candidate_code.endswith("\n") else candidate_code + "\n")
    written.append(mod_rel)

    bench_rel = target["benchmark"]
    bench_abs = os.path.join(main_repo, bench_rel)
    text = open(bench_abs, encoding="utf-8").read()
    with open(bench_abs, "w", encoding="utf-8") as f:
        f.write(append_entry(text, benchmark_entry))
    written.append(bench_rel)
    return written
