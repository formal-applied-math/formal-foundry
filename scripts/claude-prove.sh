#!/usr/bin/env bash
# The FRONTIER prover arm: a headless Claude Code session driving the same lean-lsp MCP
# server `leanstral-vibe.sh` gives Leanstral. Same contract, same tools, same doctrine —
# so a cron/decompose run differs from the incumbent in exactly one variable, the model.
#
# Why this exists. The foundry drafts with a frontier model and proves with a small
# specialist one, and its own cited ablation (backlog 2026-07-23, AlphaProof Nexus) reads
# frontier basic loop 9/9, small-model basic loops 0/9, specialist prover alone 0/9 —
# "drafter model class dominates architecture". That finding was applied to the draft
# stage and withheld from the prove stage. Four passes in the project's lifetime, all in
# a nine-day window in July, none since. This is the arm that makes the comparison
# runnable instead of assumed.
#
# The gates do not change. Kernel verification, axiom checks, vacuity/disproof, the
# faithfulness judge and the necessity prober are the foundry's actual value and they
# are engine-independent. Only the prover is swapped.
#
# Contract (identical to leanstral-vibe.sh, so vibe_prove.py needs no special case):
#   claude-prove.sh --agent lean --auto-approve --max-turns N -p "TASK"
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"

# the pack names the library; the foundry does not (runbook 06)
eval "$(python3 "$FOUNDRY/probe/domain_pack.py" --export-env ${DOMAIN:+"$DOMAIN"})"
MAIN="${MAIN_REPO:-$(dirname "$FOUNDRY")/$DOMAIN_REPO_NAME}"
BASE="$MAIN/docker/docker-compose.yml"
LSP="$MAIN/docker/docker-compose.lean-lsp.yml"

# 1. Parse and VALIDATE before touching docker. This ordering is load-bearing: the
#    flip stops another process's daemon, and an unusable command line must never cost
#    someone the Lean slot. Learned the hard way 2026-09-09 — a smoke test with
#    deliberately incomplete args was expected to be a no-op and instead took the slot
#    out from under a concurrent session mid-run. leanstral-vibe.sh has the same
#    stop-then-parse ordering and the same latent hazard.
TURNS=60; TASK=""; take=0
for a in "$@"; do
  if [ "$take" = "turns" ]; then TURNS="$a"; take=0; continue; fi
  if [ "$take" = "prompt" ]; then TASK="$a"; take=0; continue; fi
  case "$a" in
    --max-turns) take=turns ;;
    -p|--prompt) take=prompt ;;
    --agent|--auto-approve) ;;          # vibe-isms with no Claude equivalent
    *) ;;
  esac
done
[ -n "$TASK" ] || { echo "[claude-prove] no -p prompt given (nothing flipped)" >&2; exit 2; }
case "$TURNS" in ''|*[!0-9]*) echo "[claude-prove] --max-turns must be a number, got '$TURNS' (nothing flipped)" >&2; exit 2 ;; esac
command -v claude >/dev/null || { echo "[claude-prove] claude CLI not on PATH (nothing flipped)" >&2; exit 2; }

# 2. One Lean process: stop the daemon, never `down` (that removes the shared network
#    the daemon publishes 7878 on and breaks the flip back).
if docker ps --format '{{.Names}}' | grep -q 'lean-repl'; then
  echo "[claude-prove] lean-repl daemon is UP — stopping it (one Lean process)…" >&2
  docker compose -f "$BASE" stop lean-repl
fi

# 3. Bring up the mem-capped lean-lsp service and wait for it, aborting as TRANSIENT
#    (exit 4) rather than letting a cold service read as a prover failure.
docker compose -f "$BASE" -f "$LSP" up -d lean-lsp >/dev/null
echo "[claude-prove] waiting for lean-lsp-mcp…" >&2
ready=0
for _ in $(seq 1 40); do
  if docker logs "$DOMAIN_LEAN_LSP_CONTAINER" 2>&1 | grep -q LEAN_LSP_MCP_READY; then ready=1; break; fi
  docker ps --format '{{.Names}}' | grep -q "$DOMAIN_LEAN_LSP_CONTAINER" || break
  sleep 3
done
if [ "$ready" != "1" ]; then
  echo "[claude-prove] lean-lsp-mcp not ready (timeout/crash) — aborting as transient" >&2
  exit 4
fi

# 4. The same lean-lsp MCP server vibe spawns — stdio, exec'd into the running container.
#    Both arms therefore see an identical tool surface; the only variable is the model.
MCP="$(mktemp)"; trap 'rm -f "$MCP"' EXIT
cat > "$MCP" <<JSON
{"mcpServers": {"lean-lsp": {"command": "docker",
  "args": ["exec", "-i", "${DOMAIN_LEAN_LSP_CONTAINER}",
           "lean-lsp-mcp", "--lean-project-path", "/app"]}}}
JSON

# 5. The house doctrine, injected as a system prompt — the same text leanstral-vibe.sh
#    prepends to the task. Same content, the mechanism Claude is built for.
DOCTRINE="$(python3 -c "import sys; sys.path.insert(0, '$FOUNDRY/probe'); from house_context import build_system_prompt; print(build_system_prompt('$MAIN'))")"

cd "$MAIN"
exec claude -p "$TASK" \
  --mcp-config "$MCP" \
  --append-system-prompt "$DOCTRINE" \
  --max-turns "$TURNS" \
  --permission-mode acceptEdits \
  --model "${CLAUDE_PROVER_MODEL:-sonnet}"
