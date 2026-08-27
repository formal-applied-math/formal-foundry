#!/usr/bin/env bash
# Sweep a corpus for hypotheses its theorems do not need. Daemon-only, zero tokens.
# Refuses to run while a Lean build holds the slot — one Lean-loaded process at a time.
set -euo pipefail
cd "$(dirname "$0")/.."

if docker ps --format '{{.Image}}' | grep -q 'mathfin-verify'; then
  if ! docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
    echo "refusing: a mathfin-verify build is holding the Lean slot" >&2
    exit 1
  fi
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARM="${ARM:-mathfin}"
OUT="runs/necessity-sweep/${STAMP}-${ARM}.jsonl"
mkdir -p runs/necessity-sweep
echo "[sweep] arm=${ARM} out=${OUT}"
( cd probe && python3 necessity_sweep.py --arm "${ARM}" --out "../${OUT}" "$@" )
