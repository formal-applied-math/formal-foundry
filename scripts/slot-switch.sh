#!/usr/bin/env bash
# Flip the single Lean slot to <daemon|lean-lsp>. Item I phase 2: the autonomous agentic
# tick needs lean-lsp for the agentic formalize (claude self-validates via lean-lsp MCP),
# then the daemon for the gate battery (the prover-based vacuity/disproof gates need the
# fast persistent server). `refill`'s injected slot_switch_fn calls this around the
# agentic formalize. Memory doctrine: ONE Lean process — stop the other slot; `stop` not
# `down` (preserve the 7878 network for the daemon's later restart). Mirrors the flip in
# scripts/leanstral-vibe.sh.
#
#   scripts/slot-switch.sh daemon      # gates / build_manifest / ledger
#   scripts/slot-switch.sh lean-lsp    # agentic formalize (claude + lean-lsp MCP)
set -euo pipefail
SLOT="${1:?usage: slot-switch.sh <daemon|lean-lsp>}"
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="${MAIN_REPO:-/home/rapha/code/automated_proofs_quantfin}"
BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"
export COMPOSE_PROJECT_NAME=docker

case "$SLOT" in
  daemon)
    docker compose -f "$BASE" -f "$LSP" stop lean-lsp >/dev/null 2>&1 || true
    docker compose -f "$BASE" up -d lean-repl >/dev/null
    echo "[slot] waiting for the daemon (7878)…" >&2
    for _ in $(seq 1 60); do          # ~5 min; trust the readiness probe, not a racy `docker ps`
      if python3 "$FOUNDRY/probe/wait_daemon.py" >/dev/null 2>&1; then
        echo "[slot] daemon ready" >&2; exit 0
      fi
      sleep 5
    done
    echo "::error::[slot] daemon not ready after 5 min" >&2; exit 1 ;;
  lean-lsp)
    docker compose -f "$BASE" stop lean-repl >/dev/null 2>&1 || true
    # --force-recreate ⇒ fresh container + fresh logs, so the READY grep can't match a STALE
    # LEAN_LSP_MCP_READY from a prior start (which would report ready before this start loads).
    docker compose -f "$BASE" -f "$LSP" up -d --force-recreate lean-lsp >/dev/null
    echo "[slot] waiting for LEAN_LSP_MCP_READY…" >&2
    for _ in $(seq 1 60); do          # ~5 min; a restart / transient ps-miss must not abort the wait
      if docker logs mathfin-lean-lsp 2>&1 | grep -q LEAN_LSP_MCP_READY; then
        echo "[slot] lean-lsp ready" >&2; exit 0
      fi
      sleep 5
    done
    echo "::error::[slot] lean-lsp not ready after 5 min" >&2; exit 1 ;;
  *)
    echo "[slot] unknown slot: $SLOT (use daemon|lean-lsp)" >&2; exit 2 ;;
esac
