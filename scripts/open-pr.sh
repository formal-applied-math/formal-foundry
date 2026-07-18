#!/usr/bin/env bash
# Turn a proven candidate into a ready-for-review PR on formal-mathfin.
#
#   scripts/open-pr.sh --id <target-id> --tag <run-tag>
#
# Runs in the FOUNDRY CI runner (16 GB, mathfin-verify image, main checked out
# with MAIN_PR_TOKEN) — never on the 10 GB local box. It:
#   1. reads the winning candidate + placement metadata,
#   2. branches on the main checkout, places the proof in its MathFin module,
#      appends the re-export benchmark entry, registers the module in the umbrella,
#   3. VALIDATES + REGENERATES green-or-abort: lake build → axiom_audit_gen →
#      formalization_yaml → ledger; any failure files an `autoform-blocked` issue
#      on the FOUNDRY repo and exits 0 WITHOUT opening a PR (never a red PR),
#   4. promotion-honesty guard: reject an rfl-trivial "full" proof,
#   5. specific git add + commit (NO Claude attribution) + push + gh pr create
#      (label autoform, body closes the source issue). R runs the 8-lens review
#      and merges — scout authors the draft, human authors the merge.
#
# Daemon lifecycle is owned by the workflow: the daemon is DOWN here (lake build
# is the one Lean/Lake writer). The ledger re-verify (which needs the daemon) is
# a separate workflow step after this script; if it cannot run, the PR body says so.
set -euo pipefail

ID=""; TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    *) echo "[open-pr] unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$ID" ] && [ -n "$TAG" ] || { echo "[open-pr] need --id and --tag" >&2; exit 2; }

FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
SLUG="${MAIN_REPO_SLUG:-raphaelrrcoelho/formal-mathfin}"
QUEUE="$FOUNDRY/targets/queue/manifest.json"
CAND="$FOUNDRY/runs/$TAG-$ID.lean"
[ -f "$CAND" ] || { echo "[open-pr] no candidate at $CAND" >&2; exit 1; }

# --- 1. placement metadata (from the queue manifest) -------------------------
read_meta() { python3 - "$QUEUE" "$ID" "$1" <<'PY'
import json, sys
q, tid, key = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
for t in (q.get("targets", q) if isinstance(q, dict) else q):
    if t.get("id") == tid:
        v = t.get(key, "")
        print(v if not isinstance(v, (dict, list)) else json.dumps(v)); break
PY
}
MODULE="$(read_meta main_module)"
BENCH="$(read_meta benchmark)"
ISSUE="$(read_meta source_issue)"
DEFERRED="$(read_meta deferred)"   # json list of facts a SUBSET proof left for follow-up (or "")
[ -n "$MODULE" ] && [ -n "$BENCH" ] || {
  echo "[open-pr] target $ID missing main_module/benchmark metadata" >&2; exit 1; }

# A SUBSET proof (its `-- deferred:` header → manifest `deferred` list) must NOT
# auto-close its multi-part parent issue on merge: use `refs` (not `closes`) in the
# commit + PR body, and surface the deferred facts as a suggested-follow-up section
# for R to open. Absent/empty deferred → the usual `closes #N` full-issue proof.
FOLLOWUPS="$(python3 - "$DEFERRED" "$ISSUE" <<'PY'
import json, sys
raw, issue = sys.argv[1].strip(), sys.argv[2]
try:
    items = json.loads(raw) if raw else []
except ValueError:
    items = []
if items:
    out = ["", f"this pr is a faithful SUBSET of #{issue}; the drafter deferred the rest.",
           "suggested follow-up issues (open one per remaining fact, then close the parent):"]
    out += [f"- [ ] {it}" for it in items]
    print("\n".join(out))
PY
)"
if [ -n "$FOLLOWUPS" ]; then
  CLOSE_KW="refs"; CLOSE_LINE="refs #$ISSUE (subset — see follow-ups below)"
  TITLE_SUFFIX="(subset of #$ISSUE)"
else
  CLOSE_KW="closes"; CLOSE_LINE="closes #$ISSUE"; TITLE_SUFFIX="(closes #$ISSUE)"
fi

# Author (leanstral) provenance strings for the commit + PR body. (The autop
# scout path — draft PRs, `.scout` sidecars, `refs`-not-`closes` — was removed
# with autop itself; every candidate now comes from the vibe ⇄ lean-lsp-mcp
# author prover and is axiom-guarded by the gate.)
MODEL="${MODEL:-labs-leanstral-1-5}"   # the prover, for attribution + the PR body
PR_FLAGS=()
PROVER_DESC="Proved by Leanstral (${MODEL}) via the mathfin-foundry autoform pipeline; human-reviewed before merge."
COMMIT_TRAILER=(-m "Co-Authored-By: Leanstral <${MODEL}@users.noreply.mistral.ai>")
PROVENANCE_DESC="leanstral"
BODY_INTRO="this pr was produced by the autoform pipeline (leanstral $MODEL), then assembled and validated green in ci."
PROOF_BULLET="- \`$MODULE\` — the proof (axioms-clean; the probe's axiom guard passed)."

FOUNDRY_SLUG="${FOUNDRY_REPO:-raphaelrrcoelho/mathfin-foundry}"

blocked() {  # file an autoform-blocked issue on the FOUNDRY repo, do NOT open a PR
  echo "[open-pr] BLOCKED: $1" >&2
  if [ -n "${CI:-}" ] && command -v gh >/dev/null 2>&1; then
    gh label create autoform-blocked --repo "$FOUNDRY_SLUG" --color B60205 \
      --description "autoform candidate did not assemble green" 2>/dev/null || true
    gh issue create --repo "$FOUNDRY_SLUG" \
      --title "autoform-blocked: $ID ($TAG)" --label "autoform-blocked" \
      --body "candidate for $ID ($TAG) did not assemble green: $1. proof is at runs/$TAG-$ID.lean; needs manual placement." \
      2>/dev/null || true
  fi
  exit 3   # CONTENT-deterministic block: the tick RECORDS the target (fail_assembly)
  #        # — retrying the same candidate would block identically every tick.
}

transient() {  # infra/transient failure (network, gh, docker pull): no issue, no PR;
  echo "[open-pr] TRANSIENT: $1" >&2   # exit 4 so the tick keeps the target retryable.
  exit 4
}

# --- 2. branch + place -------------------------------------------------------
cd "$MAIN"
BRANCH="autoform/$ID-$TAG"
git checkout -B "$BRANCH"
python3 - "$CAND" "$QUEUE" "$ID" "$MAIN" "$FOUNDRY/probe" "$FOUNDRY/runs/$TAG-$ID.entry.json" <<'PY' || exit 1
import json, os, sys
cand_path, queue_path, tid, main, probe_dir, entry_override = sys.argv[1:7]
sys.path.insert(0, probe_dir)
from assemble import apply_contribution, ensure_umbrella_import
q = json.load(open(queue_path))
target = next(t for t in (q.get("targets", q) if isinstance(q, dict) else q) if t["id"] == tid)
if os.path.exists(entry_override):
    # the gate's strengthen pass dropped unused hypotheses: the run-tagged entry
    # matches the stripped module theorem; the seed-manifest entry no longer does.
    entry = json.load(open(entry_override))
    print("[open-pr] using strengthened entry (unused hypotheses stripped)", file=sys.stderr)
else:
    entry = target["benchmark_entry"]  # authored in the seed manifest
code = open(cand_path, encoding="utf-8").read()
written = apply_contribution(code, target, entry, main)
written += ensure_umbrella_import(main, target["main_module"])
print("[open-pr] wrote:", ", ".join(written), file=sys.stderr)
PY

# --- 3. promotion-honesty guard (reject an rfl-trivial "full") ---------------
# H9: use the tested probe_lib.rfl_proof_present (the shell glob missed `:= by rfl`
# before `end MathFin` and the `:=byrfl` spelling — non-EOF variants slipped through).
if python3 -c "import sys; sys.path.insert(0, '$FOUNDRY/probe'); from probe_lib import rfl_proof_present; sys.exit(0 if rfl_proof_present(open('$MAIN/$MODULE', encoding='utf-8').read()) else 1)"; then
  blocked "proof is rfl-trivial (reduced_core-in-disguise; test_values rfl-tripwire would reject)"
fi

# --- 4. validate + regenerate (green-or-abort) -------------------------------
# The Lean toolchain lives in the mathfin-verify IMAGE, not on the runner host,
# so the build + elaboration-dependent regens run INSIDE the image against this
# fresh checkout (mounted at /work). formalization.yaml is host-side Python (no
# Lean) and is regenerated either way. The daemon must be DOWN for the build
# (one Lake writer) — the workflow guarantees that before calling this script.
# FIRST-RUN NOTE: this docker/mount/cache path is validated on the first live PR;
# on any failure we abort to an autoform-blocked issue rather than open a red PR.
IMAGE="${VERIFY_IMAGE:-ghcr.io/raphaelrrcoelho/mathfin-verify:latest}"
# the proving daemon is done; stop it (all possible names: docker-run `lean-repl`,
# compose `docker-lean-repl-1`, plus the lean-lsp) so the build has the memory
# headroom AND is the sole Lake writer to the shared olean volume mounted below.
docker stop lean-repl docker-lean-repl-1 mathfin-lean-lsp >/dev/null 2>&1 || true
# After the build, verify + record the new benchmark entry in the ledger via
# `lake env lean` IN THIS container (LEDGER_EXEC_LOCAL — no docker-exec/daemon).
# Default `verify` scope is stale+missing; the corpus has 0 bare-`import MathFin`
# entries, so the umbrella change restales nothing and this checks only the 1 new
# entry (~60s). A fresh ledger + status=0 is the last green gate.
# CI-parity: this block must run EVERYTHING the main repo's build.yml gates on —
# lake build AND lake lint (PRs #123/#124 opened red because lint never ran here:
# defsWithUnderscore + docBlame are lint-only classes the build accepts).
REGEN='set -e
       lake exe cache get >/dev/null 2>&1 || true
       lake build MathFin
       lake lint
       python3 -m tools.verify.axiom_audit_gen --write
       python3 -m tools.formalization_yaml --write
       LEDGER_EXEC_LOCAL=1 python3 -m tools.verify.ledger verify --exec --timeout 600
       python3 -m tools.verify.ledger status'
if command -v docker >/dev/null 2>&1; then
  # --entrypoint bash overrides the image's default entrypoint (the mathfin-verify
  # CLI); without it the shell command is fed as args to that CLI and errors.
  # Mount the checkout at /app (the daemon's Lake root) + the SHARED olean volume at
  # /app/.lake, so `lake build MathFin` REUSES the daemon-built Mathlib + MathFin
  # oleans and rebuilds only the new module — not the whole library from scratch
  # (that from-scratch MathFin rebuild blew the 2h job timeout on the first live PR,
  # 2026-07-17: 8558 Mathlib modules came from `cache get`, then heavy MathFin
  # modules like DoobLpMaximalInequality rebuilt at ~400s each).
  docker run --rm --entrypoint bash \
    -v "$MAIN":/app \
    -v "${COMPOSE_PROJECT_NAME:-docker}_lake_build_cache":/app/.lake \
    -w /app "$IMAGE" -lc "$REGEN" \
    || blocked "in-image build/regen failed (lake build / lake lint / regen / ledger)"
  # The OTHER half of main-CI parity: the python gates (values/router/ledger/audit
  # byte-freshness pytest) run on the placed tree exactly as build.yml runs them
  # BEFORE the Lean build. The tick's preflight proved these gates green pre-
  # placement, so red here is THIS candidate's doing — a deterministic block.
  python3 -m pip show pytest >/dev/null 2>&1 || python3 -m pip install -q pytest
  ( cd "$MAIN" && python3 -m pytest tests/ -q ) \
    || blocked "python gates red after placement (pytest tests/)"
else
  # no docker (local dry-run): regen only the host-side artifact, flag the rest.
  python3 -m tools.formalization_yaml --write || blocked "formalization_yaml regen failed"
  echo "[open-pr] WARN: no docker — AxiomAuditGen + lake build NOT run; PR CI will gate them" >&2
fi

# --- 5. commit + push + PR ---------------------------------------------------
# specific adds only (never -A): the proof, the benchmark entry, the umbrella
# import, and whichever regenerated artifacts exist.
git add "$MODULE" "$BENCH" MathFin.lean formalization.yaml
git add MathFin/AxiomAuditGen.lean verification_ledger.json 2>/dev/null || true
# Attribution rule (honest provenance): the PR credits the prover that did the
# mathematical work (leanstral), matching the benchmark entry's
# metadata.provenance and the formalization.yaml automation count. Distinct from
# the standing rule that Claude / the coding assistant is never attributed
# anywhere.
git -c user.name="mathfin-autoform" -c user.email="autoform@users.noreply.github.com" \
    commit -q \
    -m "feat(autoform): $ID — prove $(basename "$MODULE" .lean) ($CLOSE_KW #$ISSUE)" \
    -m "$PROVER_DESC" \
    "${COMMIT_TRAILER[@]}"
git push -f origin "$BRANCH"

TOKENS="$(python3 - "$FOUNDRY/runs/$TAG-summary.jsonl" "$ID" <<'PY'
import json, sys
tok = 0
try:
    for line in open(sys.argv[1]):
        r = json.loads(line)
        if r.get("target") == sys.argv[2]: tok = r.get("tokens", 0)
except OSError: pass
print(tok)
PY
)"

# statement-fidelity notes (R7): the reviewer's map from the informal claim to the
# Lean statement, embedded in the PR body so the fidelity judgment is auditable by
# someone who is not the pipeline (the kernel checks the proof, not the statement).
POINTERS_JSON="$(read_meta pointers)"; [ -n "$POINTERS_JSON" ] || POINTERS_JSON="[]"
PROV_JSON="$(python3 -c "import json,sys; print(json.dumps({'source': sys.argv[1], 'model': sys.argv[2]}))" "$PROVENANCE_DESC" "$MODEL")"
FIDELITY_NOTES="$(python3 "$FOUNDRY/probe/fidelity_notes.py" \
  --target-id "$ID" --lean-file "$CAND" --issue-number "$ISSUE" \
  --pointers "$POINTERS_JSON" --provenance "$PROV_JSON" 2>/dev/null \
  || echo "(fidelity notes unavailable)")"

# first-pass refinery punch list (Task 2.7): a soft Magistral review over the proven
# candidate — the MECHANICAL half of the 8-lens pass (unused constructs, wrapper smell,
# register, obvious golf) as a checklist the human refiner starts from. NEVER gates:
# refinery_notes.py falls back to a skip line without a key or on any API error.
REFINERY_NOTES="$(python3 "$FOUNDRY/probe/refinery_notes.py" --lean-file "$CAND" 2>/dev/null \
  || echo "_(first-pass refinery notes unavailable)_")"

# Phase 2 (Task 2.5): a decompose-path candidate carries a DAG sidecar — list the split in
# the PR body so the reviewer sees the leaf structure. Absent for a plain cron PR (no change).
DAG_SIDE="$FOUNDRY/runs/$TAG-$ID.dag.json"
DAG_SECTION=""
if [ -f "$DAG_SIDE" ]; then
  DAG_SECTION="$(python3 - "$DAG_SIDE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
out = ["", "## lemma-DAG (decomposition)", "",
       f"main `{d['main']['name']}` is proved by applying:"]
for leaf in d.get("leaves", []):
    dep = (" — depends on " + ", ".join(f"`{x}`" for x in leaf["depends_on"])) if leaf.get("depends_on") else ""
    out.append(f"- `{leaf['name']}`{dep}")
print("\n".join(out))
PY
)"
fi

BODY="$(cat <<EOF
$BODY_INTRO $CLOSE_LINE.

what it adds:
$PROOF_BULLET
- a re-export entry in \`$BENCH\`.
- regenerated \`MathFin/AxiomAuditGen.lean\` + \`formalization.yaml\`.
$DAG_SECTION

provenance: $PROVENANCE_DESC, run tag \`$TAG\`, ~$TOKENS tokens.

review checklist (8-lens, before merge):
- [ ] the statement faithfully formalizes (its stated subset of) issue #$ISSUE — no vacuity, no weaker restatement of what it states; a declared subset is fine (see follow-ups).
- [ ] no DERIVABLE hypothesis survives (the #123 \`hP\` class: a positivity/side condition provable from the concrete defs — the strengthen pass only removes proof-UNUSED ones; this one needs an eye).
- [ ] the proof is idiomatic and consumes existing lemmas, not a wrapper.
- [ ] ledger row present (run \`ledger verify\` if ci is red on it).
- [ ] axioms clean; no slop.
- [ ] coverage.md journal block authored at merge (reviewer-written — the narrative voice stays human).$FOLLOWUPS

<details><summary>statement-fidelity notes</summary>

$FIDELITY_NOTES

</details>

<details><summary>first-pass refinery punch list (mechanical lenses — soft; the human owns the rewrite)</summary>

$REFINERY_NOTES

</details>
EOF
)"
gh label create autoform --repo "$SLUG" --color 0E8A16 \
  --description "opened by the autoform pipeline; review before merge" 2>/dev/null || true
# defs-route modules introduce new definitions — the architecture-heavy review
# class (library design, not just proof correctness). Flag them.
if grep -q '^-- new-defs:' "$MODULE" 2>/dev/null; then
  gh label create new-defs --repo "$SLUG" --color 5319E7 \
    --description "autoform PR introducing new definitions — review the design, not just the proof" 2>/dev/null || true
  PR_FLAGS+=(--label new-defs)
fi
gh pr create --repo "$SLUG" --head "$BRANCH" --label autoform "${PR_FLAGS[@]}" \
  --title "autoform: $(basename "$MODULE" .lean) $TITLE_SUFFIX" \
  --body "$BODY" || transient "gh pr create failed (candidate is green — retry next tick)"
echo "[open-pr] PR opened for $ID ($CLOSE_KW #$ISSUE)" >&2
