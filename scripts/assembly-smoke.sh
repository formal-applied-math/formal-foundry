#!/usr/bin/env bash
# The assembly canary: run the WHOLE path from a passed candidate to "ready to push" on
# the pack's fixture theorem, and stop there. No model call, no push, no PR.
#
#   scripts/assembly-smoke.sh            # needs docker + the pack's verify image
#
# It answers one question a tick cannot answer cheaply: would a proof that passed the gate
# today still assemble green against the library as it is today? (`probe/assembly_smoke.py`
# has the story of why that needed asking.) Exit 0 = assembled green; 3 = the recipe
# refused the canary, which means it would refuse every candidate.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(python3 "$FOUNDRY/probe/domain_pack.py" --export-env ${DOMAIN:+"$DOMAIN"})"
MAIN="${MAIN_REPO:-$(dirname "$FOUNDRY")/$DOMAIN_REPO_NAME}"
TAG="smoke-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$FOUNDRY/runs"

( cd "$FOUNDRY/probe" && python3 assembly_smoke.py --main-repo "$MAIN" \
    --domain "$DOMAIN_NAME" --tag "$TAG" )
ID="cal-smoke-1"
echo "[smoke] staged $ID as $TAG — assembling (dry run)…" >&2
DRY_RUN=1 MAIN_REPO="$MAIN" QUEUE_MANIFEST="$FOUNDRY/runs/$TAG-manifest.json" \
  "$FOUNDRY/scripts/open-pr.sh" --id "$ID" --tag "$TAG"
