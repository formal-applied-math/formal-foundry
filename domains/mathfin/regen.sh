#!/usr/bin/env bash
# What formal-mathfin's own CI gates on, run on the placed tree BEFORE a PR is opened.
#
# Runs INSIDE the verify image, in the library checkout mounted at /app, with the daemon
# down (this is the one Lake writer). `scripts/open-pr.sh` calls it; a non-zero exit
# blocks the PR (an `autoform-blocked` issue on the foundry, never a red PR on the
# library). It is the pack's because every command below is this library's tooling —
# the foundry's own scripts stay library-neutral.
#
# Mirrors formal-mathfin `.github/workflows/build.yml` step for step. When that workflow
# gains a gate, it is added here; a gate that runs only there is a PR that opens red.
#
# Env: MODULE (repo-relative path of the new module), MODULE_NAME (its Lean name),
#      DOMAIN_NAMESPACE, DOMAIN_SHAKE_EXE.
set -euo pipefail

git config --global --add safe.directory /app

lake exe cache get >/dev/null 2>&1 || true
lake build "$DOMAIN_NAMESPACE"

# --- narrow the imports ---------------------------------------------------------------
# The stub imports Mathlib whole so the drafter and the prover can search all of it; the
# library's ratchet holds whole-Mathlib imports at zero. The library's own shake reads
# the built oleans and rewrites the import list to what the module uses. It minimises
# every module REACHABLE from the one named, so anything it touched besides the new
# module is put back: this PR adds one module, it does not re-shake its dependencies.
if [ -n "${DOMAIN_SHAKE_EXE:-}" ] && grep -qE '^(public )?import Mathlib$' "$MODULE"; then
  before="$(git status --porcelain | awk '{print $NF}' | sort)"
  lake exe "$DOMAIN_SHAKE_EXE" --fix --add-public --keep-implied "$MODULE_NAME"
  after="$(git status --porcelain | awk '{print $NF}' | sort)"
  comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") | while read -r f; do
    [ -n "$f" ] && [ "$f" != "$MODULE" ] && git checkout -- "$f" \
      && echo "[regen] shake also rewrote $f — restored (not this PR's module)" >&2
  done
  if grep -qE '^(public )?import Mathlib$' "$MODULE"; then
    echo "[regen] $MODULE still imports Mathlib whole after the shake" >&2
    exit 1
  fi
  echo "[regen] imports after the shake:" >&2
  grep -E '^(public )?import ' "$MODULE" >&2
  # the narrowed module must still build: a `positivity`/`norm_num` extension the proof
  # relied on is not a tracked use, and its absence shows up here, naming the module
  lake build "$DOMAIN_NAMESPACE"
fi

lake lint

python3 -m tools.verify.axiom_audit_gen --write
python3 -m tools.formalization_yaml --write
# every default target, with the regenerated audit: the new constant's axiom pin is a
# `#guard_msgs` in MathFin/AxiomAuditGen.lean, and it is only checked by building it
lake build

LEDGER_EXEC_LOCAL=1 python3 -m tools.verify.ledger verify --exec --timeout 600
python3 -m tools.verify.ledger status

# the declaration index and what the ratchet reads off it: duplicate statements,
# Mathlib-shaped private lemmas, corpus entries off the program map, chain reach
lake exe mathfin_index decl-index.json 4
lake exe mathfin_index frontier-index.json 1 MathFinFrontier
python3 -m tools.verify.ratchet check --index decl-index.json
python3 -m tools.verify.decl_index check decl-index.json
rm -f decl-index.json frontier-index.json
