#!/usr/bin/env bash
# Launch a Leanstral (vibe) session wired to the Docker-plugged lean-lsp MCP.
#
# Memory doctrine: the Lean language server is the ONE local Lean process, so the
# lean-repl daemon MUST be down. This script enforces that, brings up the
# mem-capped lean-lsp service (which reuses the built oleans), sources the API
# key, then runs vibe with any args you pass through.
#
#   scripts/leanstral-vibe.sh --agent lean            # interactive
#   scripts/leanstral-vibe.sh --agent lean -p "prove the sorry in MathFin/…"  # programmatic
#
set -euo pipefail
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"

# 1. One Lean process: the lean-repl daemon must be down.
if docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
  echo "[leanstral-vibe] lean-repl daemon is UP — taking it down (one Lean process)…" >&2
  docker compose -f "$BASE" down lean-repl
fi

# 2. Bring up the mem-capped lean-lsp service (idle; loads Lean only per session).
docker compose -f "$BASE" -f "$LSP" up -d lean-lsp >/dev/null
echo "[leanstral-vibe] waiting for lean-lsp-mcp…" >&2
for _ in $(seq 1 40); do
  docker logs mathfin-lean-lsp 2>&1 | grep -q LEAN_LSP_MCP_READY && break
  sleep 3
done

# 3. Leanstral API key (from the main repo .env; never logged).
if [ -f "$MAIN/.env" ]; then set -a; . "$MAIN/.env"; set +a; export MISTRAL_API_KEY; fi
[ -n "${MISTRAL_API_KEY:-}" ] || { echo "[leanstral-vibe] MISTRAL_API_KEY not set" >&2; exit 2; }

# 4. Run vibe (Leanstral) with the lean-lsp MCP. Pass through any args.
#    First time only: run `vibe --setup` (key) and `/leanstall` (Leanstral agent).
exec vibe --trust "$@"
