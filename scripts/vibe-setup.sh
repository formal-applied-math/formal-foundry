#!/usr/bin/env bash
# Non-interactive vibe + lean-agent setup (W5 spike finding, 2026-07-17).
#
# The `lean` agent is BUNDLED in the mistral-vibe package (vibe/core/agents/models.py
# LEAN profile: model labs-leanstral-1-5, prompt vibe/core/prompts/lean.md, auth via
# the MISTRAL_API_KEY env var). `/leanstall` is an in-session slash command whose
# entire effect is `save_updates({"installed_agents": [..., "lean"]})` — no download,
# no network. So the whole non-interactive install is: install the CLI + write
# ~/.vibe/config.toml. vibe is a host-side CLI that `docker exec`s into the
# mathfin-lean-lsp container, so it runs on the RUNNER (or the local box), never
# inside the Lean image — no image rebuild needed.
#
# Idempotent. Run once locally, or per tick on the CI runner. The Leanstral key is
# read from MISTRAL_API_KEY at runtime (never written here).
set -euo pipefail
VIBE_HOME="${VIBE_HOME_DIR:-$HOME/.vibe}"

# 1. install the CLI (bundles the lean agent) if absent — uv tool if available, else pip.
if ! command -v vibe >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    uv tool install mistral-vibe
  else
    pip install --quiet mistral-vibe || pip install --quiet --break-system-packages mistral-vibe
  fi
fi

# 2. write the config: install the bundled lean agent + wire the lean-lsp MCP
#    (docker exec into the mathfin-lean-lsp container the workflow brings up).
mkdir -p "$VIBE_HOME"
cat > "$VIBE_HOME/config.toml" <<'TOML'
installed_agents = [
    "lean",
]

[[mcp_servers]]
name = "lean-lsp"
transport = "stdio"
command = "docker"
args = [
    "exec",
    "-i",
    "mathfin-lean-lsp",
    "lean-lsp-mcp",
    "--lean-project-path",
    "/app",
]
tool_timeout_sec = 600
TOML

echo "[vibe-setup] vibe $(vibe --version 2>/dev/null || echo '?') ready; lean agent installed; lean-lsp MCP wired."
[ -n "${MISTRAL_API_KEY:-}" ] || echo "[vibe-setup] NOTE: MISTRAL_API_KEY not set in env (needed at runtime)" >&2
