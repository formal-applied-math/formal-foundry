#!/usr/bin/env bash
# Build the lean_scout index the foundry's context packs consume.
#
# Runs lean_scout over the MathFin library and emits JSONL (not parquet — the
# stdlib scout_index adapter parses JSONL directly, no pyarrow) into
# foundry/index/. Rebuild once per pin; index/PIN records what it was built under.
#
# Memory doctrine: this is a full Lean build — the ONE local Lean process. The
# lean-repl daemon MUST be down. We run it in Docker (never host Lean), reusing
# the pinned toolchain image; Mathlib oleans come from `lake exe cache get` so we
# don't fight cross-project volume reuse.
#
# ┌─ FIRST-RUN SHAKEOUT ─────────────────────────────────────────────────────┐
# │ The scout-lake ↔ MathFin ↔ lean_scout Lake/Docker wiring (mount paths, the │
# │ toolchain-compat of lean_scout @5b7cdb6 against Mathlib @fabf563a) is only │
# │ fully verifiable with the Lean slot. If `lake build` errors on a Mathlib   │
# │ API drift, follow scout-lake/README.md's fallbacks (bump the lean_scout    │
# │ rev). The regex path in house_context keeps the foundry working meanwhile. │
# └───────────────────────────────────────────────────────────────────────────┘
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
IMAGE="${MATHFIN_IMAGE:-ghcr.io/raphaelrrcoelho/mathfin-verify:latest}"
INDEX="$FOUNDRY/index"
mkdir -p "$INDEX"

# 1. One Lean process: the daemon must be down.
if docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
  echo "[build-index] lean-repl daemon is UP — take it down first:" >&2
  echo "  docker compose -f $MAIN/docker/docker-compose.yml down lean-repl" >&2
  exit 2
fi

# 2. Container layout so scout-lake's '../../automated_proofs_quantfin' resolves:
#    main repo → /automated_proofs_quantfin ; scout-lake → /mathfin-foundry/scout-lake
#    Mathlib oleans via `lake exe cache get`; MathFin + BM + lean_scout compile.
MODULE="${SCOUT_MODULE:-MathFin}"
echo "[build-index] extracting types/tactics/const_dep for $MODULE (image: $IMAGE)…" >&2
docker run --rm \
  --cpuset-cpus="${VERIFY_CPUSET:-0-3}" --memory="${VERIFY_MEM:-6g}" \
  -v "$MAIN":/automated_proofs_quantfin:ro \
  -v "$FOUNDRY/scout-lake":/mathfin-foundry/scout-lake \
  -v "$INDEX":/out \
  -w /mathfin-foundry/scout-lake \
  --entrypoint bash "$IMAGE" -euo pipefail -c '
    lake exe cache get >/dev/null 2>&1 || true
    lake build
    lake run scout --command types     --jsonl --imports '"$MODULE"' > /out/types.jsonl
    lake run scout --command tactics    --jsonl --library '"$MODULE"' > /out/tactics.jsonl
    lake run scout --command const_dep  --jsonl --imports '"$MODULE"' > /out/const_dep.jsonl
  '

# 3. Stamp the pin so staleness is detectable.
{
  echo "toolchain=$(cat "$MAIN/lean-toolchain")"
  echo "lean_scout_rev=5b7cdb6b4ef4f19da644b785b96985a9a411cd65"
  echo "main_commit=$(git -C "$MAIN" rev-parse HEAD)"
  echo "module=$MODULE"
} > "$INDEX/PIN"

echo "[build-index] wrote:" >&2
wc -l "$INDEX"/*.jsonl 2>/dev/null >&2 || true
cat "$INDEX/PIN" >&2
