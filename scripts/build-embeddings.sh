#!/usr/bin/env bash
# Build the embedding vector cache the foundry's retrieval consumes, from the
# already-committed index/types.jsonl. Host-side HTTP (Mistral /v1/embeddings) —
# NO Lean process, so it needs no daemon-down guard (unlike build-index.sh).
# Rebuild when types.jsonl changes (pin bump) or the embed model changes.
set -euo pipefail
FOUNDRY="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${EMBED_MODEL:-mistral-embed}"
: "${MISTRAL_API_KEY:?set MISTRAL_API_KEY}"
cd "$FOUNDRY/probe"
python3 embed.py --model "$MODEL" --index-dir "$FOUNDRY/index"
echo "[build-embeddings] cache at index/embeddings-$MODEL.json"
