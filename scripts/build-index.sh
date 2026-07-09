#!/usr/bin/env bash
# Build the lean_scout index the foundry's context packs consume.
#
# Runs lean_scout over the MathFin library and emits JSONL (not parquet — the
# stdlib scout_index adapter parses JSONL directly) into foundry/index/.
# Rebuild once per pin; index/PIN records what it was built under.
#
# Strategy: reuse the pinned image's ALREADY-BUILT MathFin at /app (Mathlib + BM
# + MathFin oleans are baked in), add lean_scout as an EPHEMERAL in-container
# dependency, and extract there. This reuses the baked oleans (fast), never
# touches the host main repo (no stray .lake — the doctrine forbids one), and
# needs only the toolchains to match: lean_scout is pinned to 97ab10e (v4.31.0,
# our toolchain; it has no Mathlib require, so no dependency conflict).
#
# Memory doctrine: this loads the Lean/MathFin env — the ONE local Lean process.
# The lean-repl daemon MUST be down (guarded). `types` + `const_dep` read the
# built env cheaply; `tactics` RE-ELABORATES every tactic (heavy, can OOM the 6g
# cap) so it is opt-in via SCOUT_TACTICS=1.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
IMAGE="${MATHFIN_IMAGE:-ghcr.io/raphaelrrcoelho/mathfin-verify:latest}"
REV="97ab10e8a620"                       # lean_scout on Lean v4.31.0
INDEX="$FOUNDRY/index"
mkdir -p "$INDEX"

# 1. One Lean process: the daemon must be down.
if docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
  echo "[build-index] lean-repl daemon is UP — take it down first:" >&2
  echo "  docker compose -f $MAIN/docker/docker-compose.yml down lean-repl" >&2
  exit 2
fi

DO_TACTICS="${SCOUT_TACTICS:-0}"
echo "[build-index] extracting from baked /app (lean_scout @$REV; tactics=$DO_TACTICS)…" >&2

docker run --rm \
  --cpuset-cpus="${VERIFY_CPUSET:-0-3}" --memory="${VERIFY_MEM:-6g}" \
  -v "$INDEX":/out \
  -e REV="$REV" -e DO_TACTICS="$DO_TACTICS" \
  --entrypoint bash "$IMAGE" -euo pipefail -c '
    cd /app
    # add lean_scout as an ephemeral dependency of the baked MathFin project.
    # $REV comes from the container env (-e REV); the unquoted heredoc expands it.
    cat >> lakefile.lean <<EOF

require lean_scout from git
  "https://github.com/mathlib-initiative/lean_scout.git" @ "$REV"
EOF
    lake update lean_scout
    lake build lean_scout           # only lean_scout compiles; MathFin is baked
    echo "[in-container] extracting types…" >&2
    lake run scout --command types     --jsonl --imports MathFin  > /out/types.jsonl
    echo "[in-container] extracting const_dep…" >&2
    lake run scout --command const_dep --jsonl --imports MathFin  > /out/const_dep.jsonl
    if [ "$DO_TACTICS" = "1" ]; then
      echo "[in-container] extracting tactics (heavy)…" >&2
      lake run scout --command tactics --jsonl --library MathFin  > /out/tactics.jsonl
    fi
  '

# 2. Filter to MathFin.* records — the only ones the context packs ever query.
#    `--imports MathFin` extracts the whole transitive closure (~771k records /
#    ~850 MB, mostly Mathlib/core internals); MathFin itself is ~2.8k. Keeping
#    only MathFin.* shrinks the index ~275x so the adapter loads in ~0.03s.
for f in types const_dep tactics; do
  [ -f "$INDEX/$f.jsonl" ] || continue
  grep '"module":"MathFin' "$INDEX/$f.jsonl" > "$INDEX/$f.filtered" \
    && mv "$INDEX/$f.filtered" "$INDEX/$f.jsonl"
done

# 3. Stamp the pin so staleness is detectable.
{
  echo "toolchain=$(cat "$MAIN/lean-toolchain")"
  echo "lean_scout_rev=$REV"
  echo "main_commit=$(git -C "$MAIN" rev-parse HEAD)"
  echo "tactics=$DO_TACTICS"
} > "$INDEX/PIN"

echo "[build-index] wrote:" >&2
wc -l "$INDEX"/*.jsonl 2>/dev/null >&2 || true
cat "$INDEX/PIN" >&2
