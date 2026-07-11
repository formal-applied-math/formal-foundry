#!/usr/bin/env bash
# One autoformalizer pipeline tick — the headless entrypoint the GitHub Actions
# cron fires every `interval_days`. It:
#   1. asks pipeline.py whether it's due + affordable + which target (plan)
#   2. runs the prover on that target's queued stub (probe.py prove → daemon)
#   3. charges the budget + persists state (record)
#   4. STOPS at "candidate ready → notify R".  It NEVER opens a PR — scout-not-
#      author holds; a human runs the 8-lens refinery and authors the PR.
#
# The prover here is the metered text-loop probe against the lean-repl daemon
# (exact tokens, clean standalone-stub integration). vibe+lean-lsp remains the
# hands-on path for hard targets. The daemon must be UP (the workflow starts it
# in-container; locally: docker compose ... up -d lean-repl).
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
CFG="$FOUNDRY/pipeline.toml"
STATE="$FOUNDRY/pipeline_state.json"
QUEUE="$FOUNDRY/targets/queue/manifest.json"
TAG="pipeline-$(date -u +%Y%m%d)"
mkdir -p "$FOUNDRY/runs"
cd "$FOUNDRY/probe"

# API key (from main .env locally; from the secret in CI). Never logged.
if [ -f "$MAIN/.env" ]; then set -a; . "$MAIN/.env"; set +a; fi
[ -n "${MISTRAL_API_KEY:-}" ] || { echo "[tick] MISTRAL_API_KEY not set" >&2; exit 2; }

# 1. Plan.
DEC="$(python3 pipeline.py plan --config "$CFG" --state "$STATE" --queue "$QUEUE" ${FORCE:+--force})"
ACTION="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin)["action"])')"
if [ "$ACTION" != "run" ]; then
  REASON="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("reason",""))')"
  echo "[tick] skip: $REASON" >&2
  exit 0
fi
ID="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin)["target"]["id"])')"
BUDGET="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin)["budget"])')"
EFFORT="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("reasoning_effort","high"))')"
FANOUT="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("fanout",1))')"
REPAIR="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("repair_rounds",2))')"
TPA="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("tokens_per_attempt",60000))')"
echo "[tick] running target $ID (budget=$BUDGET, effort=$EFFORT, fanout=$FANOUT, repair=$REPAIR)" >&2

# 2. Prove (metered; writes runs/$TAG-summary.jsonl + winning candidate .lean).
set +e
python3 probe.py prove --manifest "$QUEUE" --only "$ID" --budget "$BUDGET" \
  --reasoning-effort "$EFFORT" --fanout "$FANOUT" --repair-rounds "$REPAIR" \
  --max-tokens "$TPA" --run-tag "$TAG" --main-repo "$MAIN"
set -e

# 3. Read the outcome + actual tokens from the run summary.
SUMMARY="$FOUNDRY/runs/$TAG-summary.jsonl"
read -r OUTCOME TOKENS < <(python3 - "$SUMMARY" "$ID" <<'PY'
import json,sys
path,tid=sys.argv[1],sys.argv[2]
out,tok="error",0
try:
    for line in open(path):
        r=json.loads(line)
        if r.get("target")==tid: out,tok=r.get("outcome","error"),int(r.get("tokens",0))
except OSError: pass
print(out,tok)
PY
)
echo "[tick] outcome=$OUTCOME tokens=$TOKENS" >&2

# 4. Record (charge actual tokens; falls back to the cap if 0).
python3 pipeline.py record --config "$CFG" --state "$STATE" --id "$ID" \
  --outcome "$OUTCOME" ${TOKENS:+--tokens "$TOKENS"} >&2

# 5. On a pass: open the ready-for-review PR on formal-mathfin (fully hands-off).
#    Gated on MAIN_PR_TOKEN — without it (any local run) we fall back to the
#    candidate-notify path, so nothing ever tries to PR without the credential.
CAND="$FOUNDRY/runs/$TAG-$ID.lean"
if [ "$OUTCOME" = "pass" ] && [ -f "$CAND" ]; then
  if [ -n "${MAIN_PR_TOKEN:-}" ]; then
    echo "[tick] pass → opening PR on formal-mathfin (closes the source issue)…" >&2
    GH_TOKEN="$MAIN_PR_TOKEN" "$FOUNDRY/scripts/open-pr.sh" --id "$ID" --tag "$TAG" \
      || echo "[tick] (open-pr.sh failed; candidate still at $CAND)" >&2
  else
    MSG="Leanstral pipeline: candidate proof for $ID is ready ($TAG).
No MAIN_PR_TOKEN in this environment, so no PR was opened. Candidate: runs/$TAG-$ID.lean · tokens: $TOKENS"
    echo "[tick] CANDIDATE READY (no token → no PR) → $CAND" >&2
    echo "$MSG" >&2
  fi
else
  echo "[tick] no candidate ($OUTCOME) — nothing to contribute this tick" >&2
fi
