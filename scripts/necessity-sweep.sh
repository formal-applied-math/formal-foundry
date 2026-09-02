#!/usr/bin/env bash
# Sweep a corpus for hypotheses its theorems do not need. Daemon-only, zero tokens.
# Refuses to run while a Lean build holds the slot — one Lean-loaded process at a time.
set -euo pipefail
cd "$(dirname "$0")/.."

# the pack names the verify image; the foundry does not (runbook 06)
eval "$(python3 probe/domain_pack.py --export-env ${DOMAIN:+"$DOMAIN"})"

if docker ps --format '{{.Image}}' | grep -q "$DOMAIN_VERIFY_IMAGE"; then
  if ! docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
    echo "refusing: a verify build is holding the Lean slot" >&2
    exit 1
  fi
fi

STAMP="$(date -u +%Y%m%d-%H%M%S)"
ARM="${ARM:-mathfin}"
OUT="runs/necessity-sweep/${STAMP}-${ARM}.jsonl"
mkdir -p runs/necessity-sweep
echo "[sweep] arm=${ARM} out=${OUT}"
( cd probe && python3 necessity_sweep.py --arm "${ARM}" --out "../${OUT}" "$@" )
