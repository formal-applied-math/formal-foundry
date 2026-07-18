"""Assemble + validate targets/manifest.json from the authored target files.

Validation: every prove-target file must elaborate WITH its sorry via the
daemon (statement well-formed; exactly 1 sorry). Run with the daemon up.

Usage: python3 build_manifest.py --main-repo /home/rapha/code/automated_proofs_quantfin
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

from decompose import build_leaf_manifest, parse_dag
from probe import daemon_check
from probe_lib import sha256_hex

STREAMS = {"bk": "backlog", "a4": "depth", "sp": "textbook", "ctl": "control"}


def parse_pointers(code: str) -> list[str]:
    """Pointers a stub declares for its context pack, via a comment line:
        -- pointers: MathFin/BlackScholes/Call.lean, MathFin/BlackScholes/Forward.lean
    Returns the repo-relative module paths (empty if none)."""
    m = re.search(r"--\s*pointers:\s*(.+)", code)
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


_META_KEYS = {
    "main-module": ("main_module", str),
    "benchmark": ("benchmark", str),
    "benchmark-id": ("benchmark_id", str),
    "source-issue": ("source_issue", lambda s: int(s.lstrip("#"))),
    # a `-- deferred: fact a; fact b` header (present only on a SUBSET proof) lists
    # the issue's facts this proof does NOT cover; open-pr surfaces them as follow-ups.
    "deferred": ("deferred", lambda s: [p.strip() for p in s.split(";") if p.strip()]),
}


def load_entry(stub_path: str) -> dict | None:
    """The re-export benchmark entry authored alongside a stub, at
    `<stub>.entry.json` — the object `assemble.apply_contribution` appends to the
    benchmark file (carrying its own `metadata.provenance`). None if absent."""
    base = stub_path[:-5] if stub_path.endswith(".lean") else stub_path
    try:
        return json.load(open(base + ".entry.json", encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_meta(code: str) -> dict:
    """Placement metadata a stub declares via comment header lines, telling the
    assembler where a proven candidate lands and which issue it closes:
        -- main-module: MathFin/FX/InterestRateParity.lean
        -- benchmark: benchmarks/mathematical_finance.json
        -- benchmark-id: mf-fx-interest-rate-parity
        -- source-issue: 108
        -- deferred: covered-interest parity band; the forward-points sign   (SUBSET only)
    Returns only the keys present (`deferred` is a list; absent when the proof
    covers the whole issue)."""
    out: dict = {}
    for raw_key, (dest, cast) in _META_KEYS.items():
        m = re.search(rf"--\s*{re.escape(raw_key)}:\s*(.+)", code)
        if m:
            out[dest] = cast(m.group(1).strip())
    return out


def _toolchain_and_commit(main_repo: str) -> tuple[str, str]:
    toolchain = open(os.path.join(main_repo, "lean-toolchain")).read().strip()
    commit = subprocess.run(["git", "-C", main_repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    return toolchain, commit


def build_dag_leaves(args) -> int:
    """--dag mode (Phase 2): write a per-leaf single-sorry manifest for a decomposed
    hard target, so the tick proves the leaves with the vibe prover VERBATIM. The DAG
    comes from `draft_decomposition` (already skeleton-gated); this only lays out the
    leaves + their parent linkage (`parent`, `parent_id`, `dag_order`)."""
    toolchain, commit = _toolchain_and_commit(args.main_repo)
    dag = parse_dag(json.load(open(args.dag)))
    meta = json.loads(args.meta) if args.meta else {}
    proved = json.loads(open(args.proved).read()) if args.proved else None
    man = build_leaf_manifest(dag, meta, args.out, toolchain=toolchain,
                              main_commit=commit, proved=proved)
    print(f"wrote {os.path.join(args.out, 'manifest.json')}: "
          f"{len(man['targets'])} leaf target(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main-repo", required=True)
    # --dag mode: build a leaf manifest for a decomposed target instead of scanning stubs.
    ap.add_argument("--dag", help="path to a skeleton-gated lemma-DAG json (Phase 2 leaf routing)")
    ap.add_argument("--out", help="output dir for the leaf manifest + stubs (--dag mode)")
    ap.add_argument("--meta", help="json placement metadata for the parent target (--dag mode)")
    ap.add_argument("--proved", help="json map leaf-name -> proved decl block, inlined (--dag mode)")
    args = ap.parse_args()
    if args.dag:
        if not args.out:
            print("--dag requires --out", file=sys.stderr)
            return 2
        return build_dag_leaves(args)

    # the live queue the scheduler reads (targets/queue/manifest.json); stubs +
    # their <id>.entry.json sidecars live alongside it.
    tdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "targets", "queue")
    toolchain, commit = _toolchain_and_commit(args.main_repo)

    targets, bad = [], []
    for path in sorted(glob.glob(os.path.join(tdir, "cal-*.lean"))):
        fname = os.path.basename(path)
        m = re.match(r"cal-(bk|a4|sp|ctl)-\d+\.lean", fname)
        if not m:
            bad.append(f"{fname}: bad name")
            continue
        code = open(path).read()
        decl = re.search(r"\btheorem\s+([A-Za-z0-9_'.]+)", code)
        if not decl:
            bad.append(f"{fname}: no theorem decl found")
            continue
        if code.count("sorry") != 1:
            bad.append(f"{fname}: expected exactly 1 sorry")
            continue
        res = daemon_check(code)
        # a well-formed statement elaborates: no errors, exactly the 1 sorry
        if res["errors"]:
            bad.append(f"{fname}: statement does not elaborate: "
                       f"{res['errors'][:2]}")
            continue
        target = {
            "id": fname[:-5], "stream": STREAMS[m.group(1)], "kind": "prove",
            "sorry_name": decl.group(1), "file": fname,
            "pointers": parse_pointers(code),
            "input_hash": sha256_hex(code + toolchain),
            **parse_meta(code),
        }
        # tag-only decompose trigger (R decision 2026-07-18): a `-- decompose` header
        # routes this hard target through the Phase 2 lemma-DAG path instead of a plain
        # prove attempt. The tick honors it only when [decompose].enabled is true.
        if re.search(r"--\s*decompose\b", code):
            target["decompose"] = True
        entry = load_entry(path)
        if entry is not None:
            target["benchmark_entry"] = entry
        targets.append(target)
        print(f"ok  {fname}  ({decl.group(1)})")

    if bad:
        print("MANIFEST BLOCKED:", *bad, sep="\n  ", file=sys.stderr)
        return 1
    manifest = {"toolchain": toolchain, "main_commit": commit,
                "targets": targets}
    out = os.path.join(tdir, "manifest.json")
    json.dump(manifest, open(out, "w"), indent=2)
    print(f"wrote {out}: {len(targets)} targets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
