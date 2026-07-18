#!/usr/bin/env bash
# The lemma-DAG decompose path for ONE hard target (Phase 2, Task 2.8). Called by
# pipeline-tick.sh only when [decompose].enabled AND the target is tagged `decompose`.
# It produces the SAME artifacts the plain path does — runs/$TAG-$ID.lean (the candidate)
# + a summary row in runs/$TAG-summary.jsonl — so the tick's downstream open-pr + record
# steps are shared and unchanged.
#
# Flow (one Lean process at a time throughout; daemon UP on entry):
#   1. draft  (daemon up): Magistral splits the target; the skeleton gate accepts/rejects
#              the split (one bounded re-decompose) BEFORE any leaf gets proving budget.
#   2. leaves (one flip pair): lean-lsp UP → vibe proves ALL leaves → flip back to the
#              daemon → gate them. (Leaves are independent in the common flat split; deep
#              depends_on chains that need keep-and-revise inlining are a later refinement.)
#   3. recompose (daemon up): assemble proved leaves + main, run the FULL gate → candidate.
#   4. record a vibe-style summary row + the A/B scoreboard row, and refresh the scoreboard.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
QUEUE="$FOUNDRY/targets/queue/manifest.json"
RUNS="$FOUNDRY/runs"
CFG="$FOUNDRY/pipeline.toml"
ID=""; TAG=""
while [ $# -gt 0 ]; do case "$1" in
  --id) ID="$2"; shift 2 ;;
  --tag) TAG="$2"; shift 2 ;;
  *) echo "[decompose] unknown arg: $1" >&2; exit 2 ;;
esac; done
[ -n "$ID" ] && [ -n "$TAG" ] || { echo "[decompose] need --id and --tag" >&2; exit 2; }
cd "$FOUNDRY/probe"

BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"
SUMMARY="$RUNS/$TAG-summary.jsonl"
LEAFMAN="$RUNS/$TAG-$ID-leaves/manifest.json"
jout() { printf '%s' "$1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" 2>/dev/null || true; }

# summary row (vibe-shaped) so pipeline-tick step 3 reads the outcome; ab scoreboard row
# + md refresh (Task 2.6). $1=outcome $2=tokens $3=leaves_total $4=leaves_closed
record() {
  python3 - "$SUMMARY" "$RUNS" "$ID" "$1" "${2:-0}" "${3:-0}" "${4:-0}" \
    "$FOUNDRY/docs/research/ab-decomposer.md" <<'PY'
import json, sys, time
sys.path.insert(0, ".")
from scoreboard import ab_row, append_ab_row, update_scoreboard_md
summ, runs, tid, outcome, tok, lt, lc, md = sys.argv[1:9]
tok, lt, lc = int(tok), int(lt), int(lc)
ts = time.strftime("%Y-%m-%dT%H:%M:%S")
with open(summ, "a", encoding="utf-8") as f:
    f.write(json.dumps({"target": tid, "harness": "decompose", "arm": "decompose",
                        "outcome": outcome, "tokens": tok, "ts": ts,
                        "leaves_total": lt, "leaves_closed": lc}) + "\n")
append_ab_row(runs, ab_row(target=tid, arm="decompose", outcome=outcome, ts=ts,
                           leaves_total=lt, leaves_closed=lc, tokens=tok))
update_scoreboard_md(md, runs)
PY
}

TURNS="$(python3 -c "import pipeline_lib as p; print(p.DecomposeConfig.load('$CFG').leaf_max_turns)" 2>/dev/null || echo 40)"
echo "[decompose] $ID → drafting a lemma-DAG (Magistral) + skeleton gate…" >&2

# 1. draft + skeleton gate (daemon up).
DRAFT="$(python3 decompose_tick.py draft --id "$ID" --tag "$TAG" --runs "$RUNS" \
  --queue "$QUEUE" --main-repo "$MAIN" --config "$CFG" || echo '{"outcome":"error"}')"
DOUT="$(jout "$DRAFT" outcome)"
LT="$(jout "$DRAFT" leaves_total)"; LT="${LT:-0}"
echo "[decompose] draft outcome=$DOUT leaves_total=$LT" >&2
if [ "$DOUT" != "drafted" ]; then
  # indeterminate = wedged daemon (retryable → error); a bad/undraftable split records so
  # the pipeline moves on (max_rounds). Either way: no candidate this tick.
  case "$DOUT" in
    indeterminate|error) record error 0 "$LT" 0 ;;
    *) record max_rounds 0 "$LT" 0 ;;
  esac
  echo "[decompose] no split to prove ($DOUT) — done" >&2
  exit 0
fi

# 2. prove ALL leaves in one flip pair (mirrors pipeline-tick.sh step 2, leaf manifest).
echo "[decompose] proving leaves via vibe ⇄ lean-lsp-mcp (max_turns=$TURNS)…" >&2
set +e
python3 vibe_prove.py run --manifest "$LEAFMAN" --arm decompose \
  --max-turns "$TURNS" --run-tag "$TAG" --main-repo "$MAIN"
echo "[decompose] flipping the Lean slot back to the daemon for the leaf gates…" >&2
docker compose -f "$BASE" -f "$LSP" stop lean-lsp >/dev/null 2>&1
docker compose -f "$BASE" -p docker up -d lean-repl >/dev/null 2>&1
python3 wait_daemon.py || echo "[decompose] WARNING: daemon not ready; gates may fail" >&2
python3 vibe_prove.py gate --manifest "$LEAFMAN" --arm decompose \
  --run-tag "$TAG" --main-repo "$MAIN"
set -e

# 3. recompose (daemon up): assemble proved leaves + main, full gate → candidate.
RECMP="$(python3 decompose_tick.py recompose --id "$ID" --tag "$TAG" --runs "$RUNS" \
  || echo '{"outcome":"fail_gate","leaves_total":0,"leaves_closed":0}')"
ROUT="$(jout "$RECMP" outcome)"
LT="$(jout "$RECMP" leaves_total)"; LT="${LT:-0}"
LC="$(jout "$RECMP" leaves_closed)"; LC="${LC:-0}"
echo "[decompose] recompose outcome=$ROUT leaves=$LC/$LT" >&2

# 4. record. `pass` → candidate at runs/$TAG-$ID.lean (pipeline-tick opens the PR). A
#    partial banks the proved leaves (run artifacts) + records so the pipeline moves on.
case "$ROUT" in
  pass)    record pass 0 "$LT" "$LC" ;;
  partial) record max_rounds 0 "$LT" "$LC" ;;   # honest partial: banked leaves, declared remainder
  *)       record fail_gate 0 "$LT" "$LC" ;;
esac
exit 0
