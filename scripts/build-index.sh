#!/usr/bin/env bash
# Build the lean_scout index the foundry's context packs consume.
#
# Runs lean_scout over the target library and emits JSONL (not parquet — the
# stdlib scout_index adapter parses JSONL directly) into foundry/index/.
# Rebuild once per pin; index/PIN records what it was built under.
#
# Strategy: reuse the pinned image's ALREADY-BUILT library at /app (Mathlib + deps
# + library oleans are baked in), add lean_scout as an EPHEMERAL in-container
# dependency, and extract there. This reuses the baked oleans (fast), never
# touches the host main repo (no stray .lake — the doctrine forbids one), and
# needs only the toolchains to match: lean_scout is pinned to 289c1f1 (v4.32.0,
# our toolchain; it has no Mathlib require, so no dependency conflict).
#
# Memory doctrine: this loads the Lean/library env — the ONE local Lean process.
# The lean-repl daemon MUST be down (guarded). `types` + `const_dep` read the
# built env cheaply; `tactics` RE-ELABORATES every tactic (heavy, can OOM the 6g
# cap) so it is opt-in via SCOUT_TACTICS=1.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
eval "$(python3 "$FOUNDRY/probe/domain_pack.py" --export-env ${DOMAIN:+"$DOMAIN"})"
# Local default: the target library checked out BESIDE the foundry, named by the
# pack. Both scripts used to hardcode a path, and they disagreed — open-pr.sh
# pointed at `/home/rapha/code/automated_proofs_quantfin`, which does not exist.
# CI always sets MAIN_REPO explicitly, so this default only bites locally, which
# is exactly where a dead path is least likely to be noticed.
MAIN="${MAIN_REPO:-$(dirname "$FOUNDRY")/$DOMAIN_REPO_NAME}"
IMAGE="${VERIFY_IMAGE:-$DOMAIN_VERIFY_IMAGE}"
REV="289c1f1f88a4"                       # lean_scout on Lean v4.32.0
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
  -e DOMAIN_NAMESPACE="$DOMAIN_NAMESPACE" \
  --entrypoint bash "$IMAGE" -euo pipefail -c '
    cd /app
    # add lean_scout as an ephemeral dependency of the baked library project.
    # EVERY $VAR below is expanded by the shell INSIDE the container, so each one
    # must be passed with `-e` above; an unpassed host variable is unset here, and
    # under `set -u` that is a hard failure (how the missing DOMAIN_NAMESPACE was
    # caught). $REV additionally feeds the unquoted heredoc below.
    # AND: this whole block is ONE single-quoted argument, so it must contain no
    # apostrophe anywhere, comments included. One closes the quote early and
    # silently drops everything after it onto the HOST shell. That has happened.
    cat >> lakefile.lean <<EOF

require lean_scout from git
  "https://github.com/mathlib-initiative/lean_scout.git" @ "$REV"
EOF
    lake update lean_scout
    lake build lean_scout           # only lean_scout compiles; the library is baked
    echo "[in-container] extracting types…" >&2
    lake run scout --command types     --jsonl --imports "$DOMAIN_NAMESPACE"  > /out/types.jsonl
    echo "[in-container] extracting const_dep…" >&2
    lake run scout --command const_dep --jsonl --imports "$DOMAIN_NAMESPACE"  > /out/const_dep.jsonl
    if [ "$DO_TACTICS" = "1" ]; then
      echo "[in-container] extracting tactics (heavy)…" >&2
      lake run scout --command tactics --jsonl --library "$DOMAIN_NAMESPACE"  > /out/tactics.jsonl
    fi
  '

# 2. Slice to the library + the Mathlib neighborhoods it actually reaches.
#    `--imports <Namespace>` extracts the whole transitive closure (~771k records /
#    ~850 MB, mostly Mathlib/core internals). This step used to keep ONLY
#    own-namespace decls only (~2.8k, a 275x shrink) for a 0.03s load — but the
#    embedding retrieval in probe/embed.py reads types.jsonl, so that filter is
#    why the drafter's semantic search had never seen a Mathlib lemma, leaving
#    an off-pin public loogle as its only Mathlib channel. index_filter keeps
#    the middle: ours in full, plus every Mathlib module hosting a constant a
#    library proof depends on. See probe/index_filter.py for the reasoning.
python3 "$FOUNDRY/probe/index_filter.py" "$INDEX"

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
