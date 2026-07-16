"""Embedding premise retrieval over the pin-accurate lean_scout `types.jsonl`.

stdlib-only (mirrors scout_index.py / probe.py): urllib for the Mistral
`/v1/embeddings` call, pure-Python cosine ranking. Design:
docs/superpowers/specs/2026-07-16-embedding-retrieval-prove-probe-design.md.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_EMBED_MODEL = "mistral-embed"


def _parse_embeddings(data: dict) -> list[list[float]]:
    """Vectors from a /v1/embeddings response, realigned to input order by
    each item's `index` (the API may return them out of order)."""
    items = sorted(data["data"], key=lambda d: d["index"])
    return [list(it["embedding"]) for it in items]


def mistral_embed(texts, *, api_key, model=DEFAULT_EMBED_MODEL,
                  base_url=DEFAULT_BASE_URL, timeout=240) -> list[list[float]]:
    """Embed `texts` via Mistral's OpenAI-compatible /v1/embeddings. Returns one
    vector per input, in input order. Retry/backoff mirrors mistral_chat."""
    body = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/embeddings", data=body,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _parse_embeddings(data)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8")[:500]
            except Exception:
                pass
            if e.code == 401:
                raise RuntimeError(f"401 from Mistral embeddings — check "
                                   f"MISTRAL_API_KEY. {detail}") from e
            if e.code in (429, 500, 502, 503) and attempt < 3:
                time.sleep(5 * (2 ** attempt)); continue
            raise RuntimeError(f"HTTP {e.code} from Mistral embeddings: {detail}") from e
        except (ValueError, KeyError) as e:
            if attempt < 3:
                time.sleep(5 * (2 ** attempt)); continue
            raise RuntimeError(f"unparseable embeddings response: {e}") from e
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt < 3:
                time.sleep(5 * (2 ** attempt)); continue
            raise RuntimeError(f"Mistral embeddings {type(e).__name__} after retries: {e}") from e
    raise RuntimeError("unreachable")
