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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--main-repo", required=True)
    args = ap.parse_args()

    tdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "targets")
    toolchain = open(os.path.join(args.main_repo, "lean-toolchain")).read().strip()
    commit = subprocess.run(["git", "-C", args.main_repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()

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
        targets.append({
            "id": fname[:-5], "stream": STREAMS[m.group(1)], "kind": "prove",
            "sorry_name": decl.group(1), "file": fname,
            "pointers": parse_pointers(code),
            "input_hash": sha256_hex(code + toolchain),
        })
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
