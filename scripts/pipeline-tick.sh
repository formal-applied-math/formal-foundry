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

# 1. Plan (a helper, so we can re-plan after a refill).
plan() { python3 pipeline.py plan --config "$CFG" --state "$STATE" --queue "$QUEUE" ${FORCE:+--force}; }
jget() { printf '%s' "$1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" 2>/dev/null || true; }
DEC="$(plan)"
ACTION="$(jget "$DEC" action)"
REASON="$(jget "$DEC" reason)"

# 1b. Self-feeding refill: when the queue has no unattempted target, autoformalize
# one from the next `status:ready`+`type:proof` issue — magistral drafts + judges +
# roundtrips a faithful stub, leanstral runs the kernel gates — then rebuild the
# manifest and re-plan. Needs the daemon (up) + MISTRAL_API_KEY (sourced above);
# gated on [autoformalize].enabled. On any failure it falls through to the skip.
if [ "$ACTION" = "skip" ] && [ "$REASON" = "no_unattempted_targets" ]; then
  AF="$(python3 -c "import sys,json,pipeline_lib as p; c=p.AutoformalizeConfig.load(sys.argv[1]); print(json.dumps({'enabled':c.enabled,'budget':c.budget,'max_issues':c.max_issues}))" "$CFG" 2>/dev/null || echo '{}')"
  if [ "$(jget "$AF" enabled)" = "True" ]; then
    echo "[tick] queue has no unattempted target → autoformalize refill…" >&2
    REFILL="$(GH_TOKEN="${MAIN_PR_TOKEN:-${GH_TOKEN:-}}" python3 autoformalize.py refill \
      --main-repo "$MAIN" --budget "$(jget "$AF" budget)" --max-issues "$(jget "$AF" max_issues)" \
      2>>/dev/stderr || echo '{"seeded":[]}')"
    SEEDED="$(printf '%s' "$REFILL" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("seeded",[])))' 2>/dev/null || echo 0)"
    echo "[tick] refill seeded=$SEEDED" >&2
    if [ "$SEEDED" != "0" ]; then
      python3 build_manifest.py --main-repo "$MAIN" >&2 || echo "[tick] build_manifest failed post-refill" >&2
      DEC="$(plan)"; ACTION="$(jget "$DEC" action)"; REASON="$(jget "$DEC" reason)"
    fi
  fi
fi

if [ "$ACTION" != "run" ]; then
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

# 4. On a pass: open the ready-for-review PR FIRST (before recording), so a PR
#    failure leaves the target retryable. Gated on MAIN_PR_TOKEN — without it
#    (any local run) we fall back to candidate-notify and never try to PR.
CAND="$FOUNDRY/runs/$TAG-$ID.lean"
PR_OPENED=0
if [ "$OUTCOME" = "pass" ] && [ -f "$CAND" ]; then
  if [ -n "${MAIN_PR_TOKEN:-}" ]; then
    echo "[tick] pass → opening PR on formal-mathfin (closes the source issue)…" >&2
    if GH_TOKEN="$MAIN_PR_TOKEN" "$FOUNDRY/scripts/open-pr.sh" --id "$ID" --tag "$TAG"; then
      PR_OPENED=1
    else
      echo "[tick] (open-pr.sh did not open a PR; candidate still at $CAND)" >&2
    fi
  else
    echo "[tick] CANDIDATE READY (no MAIN_PR_TOKEN → no PR) → $CAND · tokens: $TOKENS" >&2
  fi
else
  echo "[tick] no candidate ($OUTCOME) — nothing to contribute this tick" >&2
fi

# 5. Record — charge tokens + mark the target done, EXCEPT when it should stay
#    retryable: an infra `error`, or a `pass` whose PR did not open (so the next
#    tick retries it). A genuine prove-failure (max_rounds/budget_exhausted) DOES
#    record, so the pipeline moves on instead of re-spending on a hard target.
if [ "$OUTCOME" = "error" ]; then
  echo "[tick] outcome=error → NOT recording (retryable next tick)" >&2
elif [ "$OUTCOME" = "pass" ] && [ "$PR_OPENED" = 0 ] && [ -n "${MAIN_PR_TOKEN:-}" ]; then
  echo "[tick] pass but PR not opened → NOT recording (retryable next tick)" >&2
else
  python3 pipeline.py record --config "$CFG" --state "$STATE" --id "$ID" \
    --outcome "$OUTCOME" ${TOKENS:+--tokens "$TOKENS"} >&2
fi
