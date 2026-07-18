#!/usr/bin/env bash
# Launch a Leanstral (vibe) session wired to the Docker-plugged lean-lsp MCP,
# with the house doctrine (values + idioms + pins) prepended to the prompt so a
# vibe session is equipped exactly like the text-loop probe.
#
# Memory doctrine: the Lean language server is the ONE local Lean process, so the
# lean-repl daemon MUST be down. This script enforces that, brings up the
# mem-capped lean-lsp service (reusing the built oleans), sources the API key,
# builds the doctrine, and runs vibe.
#
#   scripts/leanstral-vibe.sh --agent lean --auto-approve --max-turns 40 \
#       -p "TASK: prove the sorry in MathFin/…. Pointers: … . Use lean_goal."
#
# The doctrine is injected only in programmatic (-p / --prompt) mode; interactive
# sessions run without it (paste it, or start the session with it).
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"

# 1. One Lean process: the lean-repl daemon must be down. Use `stop`, never `down`:
# `docker compose down` removes the shared `docker_default` network the daemon
# publishes 7878 on, which breaks its later restart ("network not found"). `stop`
# leaves the container + network intact so the tick can flip the slot back cleanly.
if docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
  echo "[leanstral-vibe] lean-repl daemon is UP — stopping it (one Lean process)…" >&2
  docker compose -f "$BASE" stop lean-repl
fi

# 2. Bring up the mem-capped lean-lsp service (idle; loads Lean only per session).
docker compose -f "$BASE" -f "$LSP" up -d lean-lsp >/dev/null
echo "[leanstral-vibe] waiting for lean-lsp-mcp…" >&2
# H9: track readiness explicitly and ABORT-AS-TRANSIENT (exit 4) if it never comes
# up — the old loop proceeded regardless, so a crashed/never-ready lean-lsp produced
# an opaque run failure. Fail fast too if the container has died.
ready=0
for _ in $(seq 1 40); do
  if docker logs mathfin-lean-lsp 2>&1 | grep -q LEAN_LSP_MCP_READY; then ready=1; break; fi
  docker ps --format '{{.Names}}' | grep -q mathfin-lean-lsp || break   # container died
  sleep 3
done
if [ "$ready" != "1" ]; then
  echo "[leanstral-vibe] lean-lsp-mcp not ready (timeout/crash) — aborting as transient" >&2
  exit 4
fi

# 3. Leanstral API key (from the main repo .env; never logged).
if [ -f "$MAIN/.env" ]; then set -a; . "$MAIN/.env"; set +a; export MISTRAL_API_KEY; fi
[ -n "${MISTRAL_API_KEY:-}" ] || { echo "[leanstral-vibe] MISTRAL_API_KEY not set" >&2; exit 2; }

# 4. House doctrine (live values + idioms + pins), prepended to the -p prompt.
DOCTRINE="$(python3 -c "import sys; sys.path.insert(0, '$FOUNDRY/probe'); from house_context import build_system_prompt; print(build_system_prompt('$MAIN'))")"
args=(); take_prompt=0; injected=0
for a in "$@"; do
  if [ "$take_prompt" = 1 ]; then
    args+=("${DOCTRINE}"$'\n\n════════════════════\nTASK\n════════════════════\n'"${a}")
    take_prompt=0; injected=1; continue
  fi
  case "$a" in
    -p|--prompt) args+=("$a"); take_prompt=1 ;;
    *) args+=("$a") ;;
  esac
done
[ "$injected" = 1 ] || echo "[leanstral-vibe] note: no -p prompt found — running WITHOUT the house doctrine (interactive mode). Pass -p \"…\" to inject it." >&2

# 5. Run vibe (Leanstral) with the lean-lsp MCP.
#    First time only: `vibe --setup` (key) and, inside vibe, /leanstall (agent).
exec vibe --trust "${args[@]}"
