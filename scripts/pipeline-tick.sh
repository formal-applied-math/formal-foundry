#!/usr/bin/env bash
# One autoformalizer pipeline tick — the headless entrypoint the GitHub Actions
# cron fires every `interval_days`. It:
#   1. asks pipeline.py whether it's due + affordable + which target (plan)
#   2. proves that target's queued stub with the vibe ⇄ lean-lsp-mcp harness
#   3. charges the budget + persists state (record)
#   4. STOPS at "candidate ready → notify R".  It NEVER opens a PR — scout-not-
#      author holds; a human runs the 8-lens refinery and authors the PR.
#
# The prover is now the vibe ⇄ lean-lsp-mcp harness Leanstral was trained for: a
# headless agent driving live goal states + lean_multi_attempt + search, run for one
# deep session per target (depth over breadth). The tick flips the single Lean slot
# lean-lsp→daemon to gate the captured proof. Token metering is traded for a turn
# budget (max_turns); the daemon serves refill + the gate.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"

# --- domain pack (runbook 06): the ONE place that knows which library we target ---
# `DOMAIN` picks the pack; with none set the shim reads `[domain] name` from
# pipeline.toml. Exports DOMAIN_NAMESPACE, DOMAIN_LAKE_ROOT, MAIN_REPO_SLUG,
# DOMAIN_VERIFY_IMAGE, DOMAIN_LEAN_LSP_CONTAINER, DOMAIN_BENCHMARK, ...
eval "$(python3 "$FOUNDRY/probe/domain_pack.py" --export-env ${DOMAIN:+"$DOMAIN"})"
MAIN="${MAIN_REPO:-$(dirname "$FOUNDRY")/$DOMAIN_REPO_NAME}"
CFG="$FOUNDRY/pipeline.toml"
STATE="$FOUNDRY/pipeline_state.json"
QUEUE="$FOUNDRY/targets/queue/manifest.json"
# per-INVOCATION tag: two ticks on the same day must never share artifacts —
# run 5 gated run 4's telemetry-committed .candidate under the shared day-tag
# after its own prove phase had failed, replaying a stale (broken) module.
TAG="pipeline-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$FOUNDRY/runs"
cd "$FOUNDRY/probe"

# One row per tick in runs/ticks.jsonl, whatever happened — a skip, a crash, a pass.
# `pipeline_state.json` only grows when an outcome is RECORDED, so the ticks that did
# nothing (a blocked manifest, a crashed prover) left no trace there; that is how two
# months without a single prover session read as a quiet queue. health.py reads this.
TICK_ACTION=""; TICK_REASON=""; TICK_TARGET=""; TICK_OUTCOME=""; ENGINE=""
INFRA_FAIL=0; INFRA_WHY=""
record_tick() {
  local rc=$?
  python3 "$FOUNDRY/probe/health.py" record-tick --ticks "$FOUNDRY/runs/ticks.jsonl" \
    --tag "$TAG" --action "$TICK_ACTION" --reason "$TICK_REASON" --target "$TICK_TARGET" \
    --outcome "$TICK_OUTCOME" --engine "$ENGINE" --infra-failure "$INFRA_FAIL" \
    --infra-reason "$INFRA_WHY" --exit-code "$rc" || true
}
trap record_tick EXIT

# API key (from main .env locally; from the secret in CI). Never logged.
if [ -f "$MAIN/.env" ]; then set -a; . "$MAIN/.env"; set +a; fi
# Which model proves ([prover] engine). Only the Leanstral engine needs the Mistral key;
# the Claude engine's auth is checked for real by the canary before any target is tried.
ENGINE="$(python3 -c "import pipeline_lib as p; print(p.ProverConfig.load('$CFG').engine)")"
if [ "$ENGINE" = "leanstral" ] && [ -z "${MISTRAL_API_KEY:-}" ]; then
  TICK_REASON="MISTRAL_API_KEY not set (engine=leanstral)"
  echo "[tick] $TICK_REASON" >&2; exit 2
fi

# 0. Preflight: the main repo's python gates (values/router/ledger/audit pytest)
# must be green BEFORE we spend a prove. A repo-WIDE red gate (values-review
# cadence tripped, stale ledger) would deterministically fail every candidate at
# open-pr — that is a human's red light on the repo, not a per-target failure,
# so the tick stands down instead of burning prove runs into blocked PRs.
python3 -m pip show pytest >/dev/null 2>&1 || python3 -m pip install -q pytest
if ! ( cd "$MAIN" && python3 -m pytest tests/ -q ) >"$FOUNDRY/runs/preflight-pytest.log" 2>&1; then
  echo "[tick] skip: main repo python gates are RED (see runs/preflight-pytest.log):" >&2
  tail -3 "$FOUNDRY/runs/preflight-pytest.log" >&2
  TICK_ACTION="skip"; TICK_REASON="preflight_red"
  exit 0
fi

# 0b. The ISSUE is the state machine — reconcile it BEFORE planning, so the refill
# reads a truthful backlog. Without this the pipeline only ever writes state forward
# (seed → in-progress, PR → review) and nothing walks it back: a PR closed unmerged,
# a retired stub, or a fail-open label write that did not stick each strand a target
# outside `status:ready` permanently. It also repairs the reverse drift, which is the
# one that actually bit — six stubs staged while every issue still said `ready`, so
# the refill kept re-drafting work already in the queue.
#
# Never fails a tick: this is bookkeeping, and it refuses to act on a lookup it could
# not complete. Human statuses (blocked-design, needs-triage, ...) are never touched.
GH_TOKEN="${MAIN_PR_TOKEN:-${GH_TOKEN:-}}" python3 issue_state.py \
  --repo "$MAIN_REPO_SLUG" --reconcile --queue "$(dirname "$QUEUE")" --apply \
  >/dev/null 2>>/dev/stderr || echo "[tick] issue reconcile failed — continuing" >&2

# 1. Plan (a helper, so we can re-plan after a refill).
plan() { python3 pipeline.py plan --config "$CFG" --state "$STATE" --queue "$QUEUE" ${FORCE:+--force}; }
jget() { printf '%s' "$1" | python3 -c "import sys,json;print(json.load(sys.stdin).get('$2',''))" 2>/dev/null || true; }
DEC="$(plan)"
ACTION="$(jget "$DEC" action)"
REASON="$(jget "$DEC" reason)"

# The manifest is a cache of the stubs on disk. If stubs exist that it does not list,
# rebuild it BEFORE deciding the queue is empty — otherwise the refill drafts another
# stub while the ones already staged are never activated (2026-07-29 .. 08-18).
# build_manifest quarantines a bad stub alone; it exits non-zero only when the daemon
# could not answer, which is an infrastructure failure, not an empty queue.
rebuild_manifest() {
  if ! python3 build_manifest.py --main-repo "$MAIN" >&2; then
    INFRA_FAIL=1; INFRA_WHY="build_manifest could not validate the queue (daemon)"
    echo "::error::[tick] $INFRA_WHY" >&2
    return 1
  fi
}
if [ "$(jget "$DEC" stale_manifest)" = "True" ]; then
  echo "[tick] manifest is stale — rebuilding it before planning again" >&2
  if rebuild_manifest; then
    DEC="$(plan)"; ACTION="$(jget "$DEC" action)"; REASON="$(jget "$DEC" reason)"
  fi
fi

# 1b. Self-feeding refill: when the queue has no unattempted target, autoformalize
# one from the next `status:ready`+`type:proof` issue — claude drafts + judges +
# roundtrips a faithful stub, leanstral runs the kernel gates — then rebuild the
# manifest and re-plan. Needs the daemon (up) + MISTRAL_API_KEY (sourced above);
# gated on [autoformalize].enabled. On any failure it falls through to the skip.
# (not after an infrastructure failure: drafting against a daemon that could not
# validate the queue spends a Claude session on gates that cannot answer)
if [ "$ACTION" = "skip" ] && [ "$REASON" = "no_unattempted_targets" ] && [ "$INFRA_FAIL" = 0 ]; then
  AF="$(python3 -c "import sys,json,pipeline_lib as p; c=p.AutoformalizeConfig.load(sys.argv[1]); print(json.dumps({'enabled':c.enabled,'budget':c.budget,'max_issues':c.max_issues}))" "$CFG" 2>/dev/null || echo '{}')"
  if [ "$(jget "$AF" enabled)" = "True" ]; then
    echo "[tick] queue has no unattempted target → autoformalize refill…" >&2
    # H9: a refill CRASH (non-zero exit) must be distinguishable from a legitimately
    # empty backlog — else a broken refiller reads as "nothing to do" every tick.
    REFILL="$(GH_TOKEN="${MAIN_PR_TOKEN:-${GH_TOKEN:-}}" python3 autoformalize.py refill \
      --main-repo "$MAIN" --budget "$(jget "$AF" budget)" --max-issues "$(jget "$AF" max_issues)" \
      2>>/dev/stderr || echo '{"seeded":[],"refill_error":true}')"
    if printf '%s' "$REFILL" | python3 -c 'import sys,json; sys.exit(0 if json.load(sys.stdin).get("refill_error") else 1)' 2>/dev/null; then
      echo "[tick] refill CRASHED (see stderr) — NOT an empty backlog; retryable next tick" >&2
    fi
    SEEDED="$(printf '%s' "$REFILL" | python3 -c 'import sys,json;print(len(json.load(sys.stdin).get("seeded",[])))' 2>/dev/null || echo 0)"
    echo "[tick] refill seeded=$SEEDED" >&2
    # H5: attempts whose gates went indeterminate (wedged daemon) are retryable, not
    # rejections — log them distinctly so a daemon-infra tick is not read as "no targets".
    INDET="$(printf '%s' "$REFILL" | python3 -c 'import sys,json;print(sum(1 for r in json.load(sys.stdin).get("attempted",[]) if r.get("outcome")=="indeterminate"))' 2>/dev/null || echo 0)"
    [ "$INDET" != "0" ] && echo "[tick] refill indeterminate=$INDET (daemon infra — retryable next tick)" >&2
    if [ "$SEEDED" != "0" ]; then
      if rebuild_manifest; then
        DEC="$(plan)"; ACTION="$(jget "$DEC" action)"; REASON="$(jget "$DEC" reason)"
      fi
    fi
  fi
fi

TICK_ACTION="$ACTION"; TICK_REASON="$REASON"
if [ "$ACTION" != "run" ]; then
  echo "[tick] skip: $REASON" >&2
  # an infrastructure failure on the way (a daemon that could not validate the queue)
  # is not an empty queue: the tick goes red instead of reporting a quiet skip
  if [ "$INFRA_FAIL" = 1 ]; then exit 1; fi
  exit 0
fi
ID="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin)["target"]["id"])')"
TURNS="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("max_turns",40))')"
TICK_TARGET="$ID"
echo "[tick] running target $ID via the $ENGINE prover ⇄ lean-lsp-mcp (max_turns=$TURNS)" >&2

# 2. Prove the target. Same artifacts either way (runs/$TAG-$ID.lean + a summary row) so
#    steps 3-6 are shared. Routing:
#      • plain vibe path by default (the running cron, byte-identical when decompose is off);
#      • the lemma-DAG decompose path when a workflow_dispatch FORCES it (DECOMPOSE_FORCE),
#        or the target is tagged `decompose` and [decompose].enabled;
#      • AND the autonomous standing rule: when [decompose].enabled, a plain attempt that
#        can't close the target (max_rounds/fail_gate) ESCALATES that same target to decompose.
BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"
SUMMARY="$FOUNDRY/runs/$TAG-summary.jsonl"
DECOMPOSE_TAG="$(printf '%s' "$DEC" | python3 -c 'import sys,json;print("1" if json.load(sys.stdin)["target"].get("decompose") else "")' 2>/dev/null || true)"
DECOMPOSE_ON="$(python3 -c "import pipeline_lib as p; print('1' if p.DecomposeConfig.load('$CFG').enabled else '')" 2>/dev/null || true)"
# one-shot override: `gh workflow run … -f decompose=true` sets DECOMPOSE_FORCE, routing the
# CI-SELECTED target straight to decompose (CI still picks the target; we skip the plain try).
DECOMPOSE_FORCE="${DECOMPOSE_FORCE:-}"

last_outcome() { python3 - "$SUMMARY" "$ID" <<'PY'
import json, sys
out = "error"
try:
    for line in open(sys.argv[1]):
        r = json.loads(line)
        if r.get("target") == sys.argv[2]: out = r.get("outcome", "error")
except OSError: pass
print(out)
PY
}
run_decompose() {  # decompose-tick.sh owns its own daemon↔lsp flips + records the summary + A/B rows
  "$FOUNDRY/scripts/decompose-tick.sh" --id "$ID" --tag "$TAG" \
    || echo "[tick] decompose-tick.sh failed rc=$? — reading summary for the outcome" >&2
  # its leaf run proves the canary first when this tick has not (a forced decompose)
  if python3 -c "import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get('canary_ok') is False else 1)" \
       "$FOUNDRY/runs/$TAG-foundry-canary.json" 2>/dev/null; then
    INFRA_FAIL=1; INFRA_WHY="prover canary failed in the decompose leaf run"
  fi
}

if [ -n "$DECOMPOSE_FORCE" ] || { [ -n "$DECOMPOSE_ON" ] && [ -n "$DECOMPOSE_TAG" ]; }; then
  WHY="$([ -n "$DECOMPOSE_FORCE" ] && echo 'forced by dispatch' || echo 'tagged decompose + enabled')"
  echo "[tick] $ID → lemma-DAG decompose path ($WHY)" >&2
  run_decompose
elif python3 vibe_prove.py adopt --id "$ID" --run-tag "$TAG" >&2; then
  # A proof of this target already passed the gate in an earlier tick and never became a
  # PR (a refused push, a transient assembly failure). Re-gate it on the current daemon —
  # the pins may have moved — instead of paying for a new session. The daemon is up: no
  # prover ran, so the Lean slot was never flipped.
  echo "[tick] $ID: reusing a proof that already passed the gate — re-gating it, no new session" >&2
  set +e
  python3 wait_daemon.py; DAEMON_RC=$?
  if [ "$DAEMON_RC" != 0 ]; then
    INFRA_FAIL=1; INFRA_WHY="the daemon was not ready to re-gate the adopted proof"
  else
    python3 vibe_prove.py gate --manifest "$QUEUE" --only "$ID" --run-tag "$TAG" --main-repo "$MAIN" --config "$CFG"
  fi
  set -e
else
  # Plain vibe ⇄ lean-lsp-mcp harness (spec:
  #   docs/superpowers/specs/2026-07-17-leanstral-vibe-cron-harness-design.md).
  #   Phase A (lean-lsp UP): headless vibe drives lean_goal / lean_multi_attempt / search
  #     → runs/$TAG-$ID.candidate. leanstral-vibe.sh stops the daemon + brings lean-lsp up.
  #   Flip: stop lean-lsp, restart the daemon — `stop`/`up`, NEVER `down` (down removes the
  #     shared docker_default network the daemon publishes 7878 on).
  #   Phase B (daemon UP): gate the candidate → runs/$TAG-$ID.lean + summary row.
  set +e
  python3 vibe_prove.py run --manifest "$QUEUE" --only "$ID" \
    --max-turns "$TURNS" --run-tag "$TAG" --main-repo "$MAIN" --config "$CFG"
  RUN_RC=$?
  echo "[tick] flipping the Lean slot back to the daemon for the gate…" >&2
  docker compose -f "$BASE" -f "$LSP" stop lean-lsp >/dev/null 2>&1
  docker compose -f "$BASE" -p docker up -d lean-repl >/dev/null 2>&1
  # Probe the port until the daemon actually serves — NOT `docker logs | grep READY:`,
  # which matches the stale READY from before the restart and races the cold load.
  python3 wait_daemon.py; DAEMON_RC=$?
  # A failed canary, a crashed run phase, or a daemon that did not come back means the
  # machinery is broken: nothing is gated, nothing escalates, nothing is recorded as a
  # verdict, and the tick goes RED. (Gating anyway is how a crash became `max_rounds`.)
  if [ "$RUN_RC" = 5 ]; then
    INFRA_FAIL=1; INFRA_WHY="prover canary failed (runs/$TAG-foundry-canary.*)"
  elif [ "$RUN_RC" != 0 ]; then
    INFRA_FAIL=1; INFRA_WHY="the prove phase exited rc=$RUN_RC"
  elif [ "$DAEMON_RC" != 0 ]; then
    INFRA_FAIL=1; INFRA_WHY="the daemon did not come back for the gate"
  else
    python3 vibe_prove.py gate --manifest "$QUEUE" --only "$ID" --run-tag "$TAG" --main-repo "$MAIN" --config "$CFG"
  fi
  set -e
  if [ "$INFRA_FAIL" = 1 ]; then echo "::error::[tick] $INFRA_WHY — no verdict this tick" >&2; fi
  # Autonomous escalation (the standing rule): the plain path couldn't close it → escalate the
  # SAME target to decompose. Its summary row (written last) supersedes the plain one at step 3.
  if [ -n "$DECOMPOSE_ON" ] && [ "$INFRA_FAIL" = 0 ]; then
    case "$(last_outcome)" in
      max_rounds|fail_gate)
        echo "[tick] plain path did not close $ID → escalating to the decompose path" >&2
        run_decompose ;;
    esac
  fi
fi

# 3. Read the final outcome + tokens (the LAST row for $ID — a decompose escalation's row
#    supersedes the plain attempt's; SUMMARY defined in step 2).
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
TICK_OUTCOME="$OUTCOME"
# `error` is the machinery failing on this target — the prover did not run, or the daemon
# could not gate — never a verdict. It stays retryable (not recorded below) AND turns the
# tick red, so one failed target is visible the day it happens.
if [ "$OUTCOME" = "error" ] && [ "$INFRA_FAIL" = 0 ]; then
  INFRA_FAIL=1; INFRA_WHY="no verdict on $ID: the prover did not run or the gate could not (runs/$TAG-$ID.session.json)"
fi

# 4. On a pass: open the ready-for-review PR FIRST (before recording), so a PR
#    failure leaves the target retryable. Gated on MAIN_PR_TOKEN — without it
#    (any local run) we fall back to candidate-notify and never try to PR.
CAND="$FOUNDRY/runs/$TAG-$ID.lean"
PR_OPENED=0
ASSEMBLY_BLOCKED=0
if [ "$OUTCOME" = "pass" ] && [ -f "$CAND" ]; then
  if [ -n "${MAIN_PR_TOKEN:-}" ]; then
    echo "[tick] $OUTCOME → opening PR on $MAIN_REPO_SLUG…" >&2
    if GH_TOKEN="$MAIN_PR_TOKEN" "$FOUNDRY/scripts/open-pr.sh" --id "$ID" --tag "$TAG"; then
      PR_OPENED=1
    else
      OPENPR_RC=$?
      if [ "$OPENPR_RC" = 3 ]; then
        # content-deterministic block (regen/lint/python gates rejected the
        # candidate): retrying the SAME stub re-blocks every tick — record it.
        ASSEMBLY_BLOCKED=1
        echo "[tick] open-pr content-BLOCKED (rc=3) → will record fail_assembly" >&2
      else
        echo "[tick] (open-pr.sh transient failure rc=$OPENPR_RC; candidate still at $CAND — retryable)" >&2
        # A proof that passed and could not become a PR is the machinery failing (a push
        # refused with 403 went green on 2026-09-28). Red, and the next tick REUSES the
        # verified proof (`vibe_prove.py adopt`) instead of paying for it again.
        INFRA_FAIL=1
        INFRA_WHY="$ID passed but its PR could not be opened (open-pr rc=$OPENPR_RC); the verified proof is kept for the next tick"
      fi
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
elif [ "$OUTCOME" = "pass" ] && [ "$PR_OPENED" = 0 ] && [ "$ASSEMBLY_BLOCKED" = 1 ]; then
  # the proof passed but assembly rejected the CONTENT (lint/regen/python gates):
  # record so the pipeline moves on; the issue re-draws later under current gates.
  echo "[tick] pass but content-blocked at assembly → recording fail_assembly" >&2
  python3 pipeline.py record --config "$CFG" --state "$STATE" --id "$ID" \
    --outcome fail_assembly ${TOKENS:+--tokens "$TOKENS"} >&2
elif [ "$OUTCOME" = "pass" ] && [ "$PR_OPENED" = 0 ] && [ -n "${MAIN_PR_TOKEN:-}" ]; then
  echo "[tick] pass but PR not opened (transient) → NOT recording (retryable next tick)" >&2
else
  python3 pipeline.py record --config "$CFG" --state "$STATE" --id "$ID" \
    --outcome "$OUTCOME" ${TOKENS:+--tokens "$TOKENS"} >&2
fi

# 6. Obstruction-family triage (Task 1.7) — the standing feedback signal. Rewrite
#    runs/obstructions-report.md from the drafter's refill-history + the prover's
#    run summaries and print the dominant family, so every tick names the fix the
#    pipeline needs next. Non-fatal (a bad artifact never fails the tick); the
#    report rides the runs/ telemetry the persist step pushes.
python3 - "$FOUNDRY/runs" >&2 <<'PY' || echo "[tick] obstruction triage skipped" >&2
import sys
from obstructions import census, render_report
runs = sys.argv[1]
buckets = census(runs)   # computed off the provenance substrate (deep wiring)
with open(runs + "/obstructions-report.md", "w", encoding="utf-8") as f:
    f.write(render_report(buckets))
top = max(buckets.items(), key=lambda kv: kv[1]["count"])
print(f"[tick] obstructions: {top[0]} leads ({top[1]['count']}) — see "
      "runs/obstructions-report.md" if top[1]["count"] else
      "[tick] obstructions: none recorded yet")
PY

# A tick whose machinery failed exits non-zero, so the run is RED (the persist step still
# commits its evidence, `if: always()`). A prover that ran and could not close a target
# is not a failure of the tick; a prover that never ran is.
if [ "$INFRA_FAIL" = 1 ]; then
  echo "::error::[tick] infrastructure failure: $INFRA_WHY" >&2
  exit 1
fi
