#!/usr/bin/env bash
# Assemble a CONTRIBUTION PACKET from a passed (and refined) pipeline run, so R's
# PR into the main repo is mechanical rather than archaeology. This is the
# foundry→main seam: the foundry reads main's dynamics (pins, issues, library)
# and hands back a packet that already fits main's contracts. It NEVER touches
# main — R authors the PR (scout-not-author).
#
#   scripts/contribute.sh --tag pipeline-20260709 --id cal-bk-1 \
#       --issue 53 --module MathFin/BlackScholes/BarrierParity.lean
#
# Emits contrib/<id>/{candidate.lean, provenance.yaml, PR.md}.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
TAG=""; ID=""; ISSUE=""; MODULE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2;;
    --id) ID="$2"; shift 2;;
    --issue) ISSUE="$2"; shift 2;;
    --module) MODULE="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -n "$TAG" ] && [ -n "$ID" ] || { echo "usage: contribute.sh --tag T --id ID [--issue N] [--module PATH]" >&2; exit 2; }

CAND="$FOUNDRY/runs/$TAG-$ID.lean"
SUMMARY="$FOUNDRY/runs/$TAG-summary.jsonl"
[ -f "$CAND" ] || { echo "no candidate: $CAND (did the run pass?)" >&2; exit 1; }
OUT="$FOUNDRY/contrib/$ID"; mkdir -p "$OUT"
cp "$CAND" "$OUT/candidate.lean"

MODULE_DISPLAY="${MODULE:-MathFin/<Section>/<Module>.lean  (choose the home)}"
ISSUE_LINE="${ISSUE:+closes #$ISSUE}"

# provenance.yaml — the formalization.yaml automation.methods[] fragment R merges.
python3 - "$SUMMARY" "$ID" "$OUT/provenance.yaml" <<'PY'
import json, sys
summary, tid, out = sys.argv[1], sys.argv[2], sys.argv[3]
rec = {}
try:
    for line in open(summary):
        r = json.loads(line)
        if r.get("target") == tid:
            rec = r
except OSError:
    pass
wall = rec.get("wall_s", "?"); tok = rec.get("tokens", "?"); model = rec.get("model", "labs-leanstral-1-5")
with open(out, "w") as f:
    f.write(f"""# Merge into formalization.yaml → automation.methods:
- method: "machine autoformalization (Leanstral, refined)"
  models: ["{model}"]
  framework: "mathfin-foundry: text-loop probe / vibe <-> lean-lsp-mcp"
  cost:
    wall_time: "{wall}s"
    spend_usd: "0 (Mistral Labs beta)"
    hardware: "GitHub Actions runner; the model runs remotely at Mistral"
  prompting_notes: "reasoning_effort=high; {tok} tokens; passed the values gate + 8-lens refinery"
""")
print(f"wrote {out}")
PY

# PR.md — the handoff checklist so R's PR fits main's contracts.
cat > "$OUT/PR.md" <<EOF
# Contribution packet — $ID

**Candidate proof:** \`contrib/$ID/candidate.lean\`  ·  **$ISSUE_LINE**
**Destined home in main:** \`$MODULE_DISPLAY\`

## R's PR checklist (main repo — scout-not-author; you author it)
1. Move the proof into \`$MODULE_DISPLAY\` (real Lean file, not the JSON).
2. Update the benchmark JSON to \`import\` + reference the lemma; set
   \`metadata.formalization_status\`.
3. Regenerate + re-gate in the main repo:
   - \`python3 -m tools.verify.axiom_audit_gen --write\`   (pins the new constant)
   - \`python3 -m tools.verify.ledger verify\`             (re-verify the new/changed entry)
   - \`python3 -m tools.verify.coverage_report\`           (confirm the split moved)
   - \`python3 -m tools.formalization_yaml --write\`        (refresh the self-report;
        merge \`contrib/$ID/provenance.yaml\` into automation.methods first)
4. \`lake build\` green + values-review cadence if the corpus grew >12 since last.
5. Author the PR ($ISSUE_LINE).

## Provenance
See \`contrib/$ID/provenance.yaml\` (model, tokens, wall-time) — merge it into
\`formalization.yaml\`'s automation block.
EOF

echo "[contribute] packet ready → contrib/$ID/  (candidate.lean · provenance.yaml · PR.md)" >&2
ls -1 "$OUT" >&2
