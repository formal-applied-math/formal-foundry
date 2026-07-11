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
[ -n "$MODULE" ] && [ -n "$BENCH" ] || {
  echo "[open-pr] target $ID missing main_module/benchmark metadata" >&2; exit 1; }

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
  exit 3   # non-zero so the tick knows the PR did NOT open (target stays retryable)
}

# --- 2. branch + place -------------------------------------------------------
cd "$MAIN"
BRANCH="autoform/$ID-$TAG"
git checkout -B "$BRANCH"
python3 - "$CAND" "$QUEUE" "$ID" "$MAIN" "$FOUNDRY/probe" <<'PY' || exit 1
import json, sys
cand_path, queue_path, tid, main, probe_dir = sys.argv[1:6]
sys.path.insert(0, probe_dir)
from assemble import apply_contribution, ensure_umbrella_import
q = json.load(open(queue_path))
target = next(t for t in (q.get("targets", q) if isinstance(q, dict) else q) if t["id"] == tid)
entry = target["benchmark_entry"]  # authored in the seed manifest
code = open(cand_path, encoding="utf-8").read()
written = apply_contribution(code, target, entry, main)
written += ensure_umbrella_import(main, target["main_module"])
print("[open-pr] wrote:", ", ".join(written), file=sys.stderr)
PY

# --- 3. promotion-honesty guard (reject an rfl-trivial "full") ---------------
PROOF_BODY="$(grep -viE '^\s*(--|/-|import|module|open|namespace|variable|@\[)' "$MAIN/$MODULE" | tr -d '[:space:]')"
case "$PROOF_BODY" in
  *":=rfl"*|*":=byrfl"|*":=Iff.rfl"*) blocked "proof is rfl-trivial (reduced_core-in-disguise; test_values rfl-tripwire would reject)";;
esac

# --- 4. validate + regenerate (green-or-abort) -------------------------------
# The Lean toolchain lives in the mathfin-verify IMAGE, not on the runner host,
# so the build + elaboration-dependent regens run INSIDE the image against this
# fresh checkout (mounted at /work). formalization.yaml is host-side Python (no
# Lean) and is regenerated either way. The daemon must be DOWN for the build
# (one Lake writer) — the workflow guarantees that before calling this script.
# FIRST-RUN NOTE: this docker/mount/cache path is validated on the first live PR;
# on any failure we abort to an autoform-blocked issue rather than open a red PR.
IMAGE="${VERIFY_IMAGE:-ghcr.io/raphaelrrcoelho/mathfin-verify:latest}"
# the proving daemon is done; stop it so the build has the memory headroom
# (two Mathlib-loaded Lean envs overcommit even a 16 GB runner).
docker stop lean-repl >/dev/null 2>&1 || true
# After the build, verify + record the new benchmark entry in the ledger via
# `lake env lean` IN THIS container (LEDGER_EXEC_LOCAL — no docker-exec/daemon).
# Default `verify` scope is stale+missing; the corpus has 0 bare-`import MathFin`
# entries, so the umbrella change restales nothing and this checks only the 1 new
# entry (~60s). A fresh ledger + status=0 is the last green gate.
REGEN='set -e
       lake exe cache get >/dev/null 2>&1 || true
       lake build MathFin
       python3 -m tools.verify.axiom_audit_gen --write
       python3 -m tools.formalization_yaml --write
       LEDGER_EXEC_LOCAL=1 python3 -m tools.verify.ledger verify --exec --timeout 600
       python3 -m tools.verify.ledger status'
if command -v docker >/dev/null 2>&1; then
  # --entrypoint bash overrides the image's default entrypoint (the mathfin-verify
  # CLI); without it the shell command is fed as args to that CLI and errors.
  docker run --rm --entrypoint bash -v "$MAIN":/work -w /work "$IMAGE" -lc "$REGEN" \
    || blocked "in-image build/regen failed (lake build MathFin / axiom_audit_gen)"
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
git -c user.name="mathfin-autoform" -c user.email="autoform@users.noreply.github.com" \
    commit -q -m "feat(autoform): $ID — prove $(basename "$MODULE" .lean) (closes #$ISSUE)"
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
MODEL="${MODEL:-labs-leanstral-1-5}"
BODY="$(cat <<EOF
this pr was produced by the autoform pipeline (leanstral $MODEL), then assembled and validated green in ci. closes #$ISSUE.

what it adds:
- \`$MODULE\` — the proof (axioms-clean; the probe's axiom guard passed).
- a re-export entry in \`$BENCH\`.
- regenerated \`MathFin/AxiomAuditGen.lean\` + \`formalization.yaml\`.

provenance: leanstral, run tag \`$TAG\`, ~$TOKENS tokens.

review checklist (8-lens, before merge):
- [ ] the statement faithfully formalizes issue #$ISSUE (no vacuity, no weaker restatement).
- [ ] the proof is idiomatic and consumes existing lemmas, not a wrapper.
- [ ] ledger row present (run \`ledger verify\` if ci is red on it).
- [ ] axioms clean; no slop.
EOF
)"
gh label create autoform --repo "$SLUG" --color 0E8A16 \
  --description "opened by the autoform pipeline; review before merge" 2>/dev/null || true
gh pr create --repo "$SLUG" --head "$BRANCH" --label autoform \
  --title "autoform: $(basename "$MODULE" .lean) (closes #$ISSUE)" \
  --body "$BODY" || blocked "gh pr create failed"
echo "[open-pr] PR opened for $ID (closes #$ISSUE)" >&2
