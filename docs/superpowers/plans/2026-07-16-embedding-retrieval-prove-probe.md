# Embedding Retrieval + Cheap Prove Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pin-accurate embedding premise retrieval to the autoformalizer and a cheap, values-safe autop prove-probe, so we strengthen the evidenced formalize failure and finally observe whether proving is a wall.

**Architecture:** A new stdlib-only `probe/embed.py` (Mistral `/v1/embeddings` client + `EmbeddingIndex` over the already-committed `index/types.jsonl`) feeds a drop-in `embedding_retrieve_fn` into the existing `retrieve_fn` seam plus a proactive top-k into the initial formalize grounding. A new isolated `probe/autop.py` runs a fixed tactic menu as whole-proof scripts through the existing daemon; autop wins are scout-tagged and open as DRAFT PRs, never silently merged. `run_target` (the tested leanstral prover) is left untouched — autop runs alongside it and only rescues targets leanstral misses.

**Tech Stack:** Python 3.11+ stdlib only (`urllib`, `json`, `hashlib`, `math`, `dataclasses`), pytest with injected `embed_fn`/`check_fn` (no network/daemon in unit tests), Mistral API (`mistral-embed`), bash for build + PR scripts.

## Global Constraints

- **Stdlib only** in `probe/` runtime code — no numpy/requests (match `scout_index.py`, `probe.py`). Cosine ranking is pure Python.
- **One Lean process locally** — embedding build + query are host-side HTTP (no Lean); the prove probe reuses the ONE existing daemon. Never spawn a second Lean-loaded process.
- **Privacy** — embeddings go only to `api.mistral.ai` (existing `MISTRAL_API_KEY`); never a cloud premise-selector. Local cosine ranking.
- **Values gate** — an autop-closed proof is a SCOUT: provenance-tagged `proof_source: "autop-<tactic>"`, opened as a DRAFT PR labeled `scout-proof`, NEVER auto-merged as a normal PR. Author-grade leanstral proofs (slop-clean + axioms-clean) merge as today.
- **No ulam code vendored** — clean-room reimplementation of the design only (their repo has no license). Cite ulam as inspiration in the spec, not in shipped code.
- **Git** — specific `git add <paths>` only, never `-A`/`.`. No `Co-Authored-By`/Claude attribution in commits. Conventional-commit subjects (`feat(...)`, `test(...)`).
- **Fails-open** — when the index or embeddings are absent, retrieval falls back to `loogle`; autop is toggleable off. A missing artifact degrades, never crashes a tick.
- **Corpus is MathFin-only** (`build-index.sh` runs `lean_scout` over MathFin) — embedding retrieval surfaces OUR premises; Mathlib-lemma discovery stays loogle's job.

All tests run from the `probe/` directory (it is on `sys.path`; existing tests use bare `import autoformalize as af`):
```bash
cd /home/rapha/code/formal-foundry/probe && python3 -m pytest <file> -v
```

---

## File Structure

- Create `probe/embed.py` — `mistral_embed` client, `_parse_embeddings`, `load_premises`, `premise_text`, `corpus_hash`, `cosine`, `top_k`, `EmbeddingIndex`, `make_embedding_retrieve_fn`, `build` CLI. One responsibility: embedding retrieval.
- Create `probe/test_embed.py` — pure tests for the above (injected `embed_fn`).
- Create `scripts/build-embeddings.sh` — one-time-per-pin vector cache build (host-side).
- Create `probe/autop.py` — `AUTOP_MENU`, `autop_candidate`, `autop_prove`. One responsibility: the cheap tactic-menu prove probe.
- Create `probe/test_autop.py` — pure tests (injected `check_fn`).
- Modify `probe/pipeline_lib.py:61-90` — add `retrieval_backend`, `retrieval_k`, `embed_model`, `autop` config fields.
- Modify `probe/autoformalize.py:517-577,780-948` — `proactive_premises` param on `formalize_with_repair`; wire embedding backend + `proactive_fn` in `refill` + main.
- Modify `probe/probe.py:296-322` — retrieval-into-prove (`context_pack`), autop rescue, scout threading into the summary + candidate write.
- Modify `scripts/open-pr.sh` — DRAFT PR + `scout-proof` label when the target's proof is a scout.
- Modify `pipeline.toml` `[autoformalize]` — surface the new keys.

---

## Task 1: Mistral embeddings client

**Files:**
- Create: `probe/embed.py`
- Test: `probe/test_embed.py`

**Interfaces:**
- Consumes: `mistral_chat` pattern from `probe/probe.py:44` (base_url, Bearer auth, retry loop).
- Produces: `mistral_embed(texts: list[str], *, api_key, model="mistral-embed", base_url="https://api.mistral.ai/v1", timeout=240) -> list[list[float]]`; `_parse_embeddings(data: dict) -> list[list[float]]`.

- [ ] **Step 1: Write the failing test**

```python
# probe/test_embed.py
"""Pure tests for embedding retrieval — injected embed_fn, no network/daemon."""
from __future__ import annotations

import embed


def test_parse_embeddings_orders_by_index():
    # /v1/embeddings may return items out of order; we must realign by "index".
    data = {"data": [
        {"index": 1, "embedding": [0.0, 1.0]},
        {"index": 0, "embedding": [1.0, 0.0]},
    ]}
    assert embed._parse_embeddings(data) == [[1.0, 0.0], [0.0, 1.0]]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd probe && python3 -m pytest test_embed.py::test_parse_embeddings_orders_by_index -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'embed'`.

- [ ] **Step 3: Write minimal implementation**

```python
# probe/embed.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd probe && python3 -m pytest test_embed.py::test_parse_embeddings_orders_by_index -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/embed.py probe/test_embed.py
git commit -m "feat(embed): Mistral /v1/embeddings client (stdlib mirror of mistral_chat)"
```

---

## Task 2: Corpus loading + cosine ranking + EmbeddingIndex

**Files:**
- Modify: `probe/embed.py`
- Test: `probe/test_embed.py`

**Interfaces:**
- Consumes: `index/types.jsonl` records `{name, module, type, docString}` (via `scout_index._load_jsonl` shape — a JSONL of dicts).
- Produces: `load_premises(index_dir) -> list[dict]`; `premise_text(rec) -> str`; `corpus_hash(premise_texts, model) -> str`; `cosine(a, b) -> float`; `top_k(query_vec, matrix, k) -> list[int]`; `class EmbeddingIndex` with `.build(embed_fn)`, `.save(path)`, `classmethod .load(path, premises, model)`, `.retrieve(query, k, embed_fn) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# append to probe/test_embed.py
import math


def test_cosine_orthogonal_and_identical():
    assert embed.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert math.isclose(embed.cosine([1.0, 1.0], [1.0, 1.0]), 1.0)


def test_cosine_zero_vector_is_zero_not_nan():
    assert embed.cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_top_k_returns_highest_cosine_indices_in_order():
    matrix = [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]
    # query aligned with row 0; row 1 is close; row 2 orthogonal
    assert embed.top_k([1.0, 0.0], matrix, k=2) == [0, 1]


def test_premise_text_is_name_colon_type():
    rec = {"name": "MathFin.zcb", "type": "ℝ → ℝ → ℝ → ℝ", "docString": "zero-coupon bond"}
    assert embed.premise_text(rec) == "MathFin.zcb : ℝ → ℝ → ℝ → ℝ"


def test_corpus_hash_changes_with_model_and_content():
    a = embed.corpus_hash(["x : ℝ"], "mistral-embed")
    assert a != embed.corpus_hash(["x : ℝ"], "codestral-embed")   # model bumps it
    assert a != embed.corpus_hash(["y : ℝ"], "mistral-embed")     # content bumps it
    assert a == embed.corpus_hash(["x : ℝ"], "mistral-embed")     # stable


def test_embedding_index_retrieve_ranks_by_query():
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ → ℝ → ℝ", "docString": ""},
                {"name": "MathFin.forwardRate", "type": "ℝ → ℝ", "docString": ""}]

    # fake embed_fn: map any text to a 2-vector keyed on whether "zcb" appears
    def fake_embed(texts):
        return [[1.0, 0.0] if "zcb" in t else [0.0, 1.0] for t in texts]

    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(fake_embed)
    out = idx.retrieve("what is the zcb price", k=1, embed_fn=fake_embed)
    assert "MathFin.zcb" in out
    assert "MathFin.forwardRate" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd probe && python3 -m pytest test_embed.py -v -k "cosine or top_k or premise_text or corpus_hash or retrieve"`
Expected: FAIL with `AttributeError: module 'embed' has no attribute 'cosine'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to probe/embed.py
import hashlib
import math
import os


def load_premises(index_dir: str) -> list[dict]:
    """The types.jsonl records (name/module/type/docString). [] if absent —
    callers then fall back to loogle."""
    path = os.path.join(index_dir, "types.jsonl")
    recs: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
    except (OSError, ValueError):
        return []
    return recs


def premise_text(rec: dict) -> str:
    """The text we embed + surface for a premise: `name : type`."""
    return f"{rec.get('name', '')} : {rec.get('type', '')}".strip()


def corpus_hash(premise_texts: list[str], model: str) -> str:
    """Cache key: (model, corpus). A pin rebuild or model change invalidates."""
    h = hashlib.sha256(model.encode("utf-8"))
    for t in premise_texts:
        h.update(b"\x00")
        h.update(t.encode("utf-8"))
    return h.hexdigest()


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 (never NaN) when either vector is zero."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def top_k(query_vec: list[float], matrix: list[list[float]], k: int) -> list[int]:
    """Indices of the k rows most cosine-similar to `query_vec`, best first."""
    scored = [(cosine(query_vec, row), i) for i, row in enumerate(matrix)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [i for _, i in scored[:k]]


class EmbeddingIndex:
    """Vectors for a premise corpus + cosine top-k retrieval. Build once per pin
    (vectors cached to disk keyed by (model, corpus_hash)); query embeds one text
    and ranks locally."""

    def __init__(self, premises: list[dict], *, model: str):
        self.premises = premises
        self.model = model
        self.texts = [premise_text(p) for p in premises]
        self.hash = corpus_hash(self.texts, model)
        self.vectors: list[list[float]] | None = None

    def build(self, embed_fn) -> "EmbeddingIndex":
        """Embed the corpus. `embed_fn(list[str]) -> list[list[float]]`."""
        self.vectors = embed_fn(self.texts)
        return self

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"model": self.model, "corpus_hash": self.hash,
                       "vectors": self.vectors}, f)

    @classmethod
    def load(cls, path: str, premises: list[dict], model: str) -> "EmbeddingIndex | None":
        """Load a cached index iff its (model, corpus_hash) matches `premises` —
        else None (stale/absent → caller rebuilds or falls back)."""
        idx = cls(premises, model=model)
        try:
            with open(path, encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, ValueError):
            return None
        if blob.get("model") != model or blob.get("corpus_hash") != idx.hash:
            return None
        idx.vectors = blob["vectors"]
        return idx

    def retrieve(self, query: str, k: int, embed_fn) -> str:
        """Top-k premises for `query` as `name : type` lines ('' if not built)."""
        if not self.vectors:
            return ""
        qv = embed_fn([query])[0]
        idxs = top_k(qv, self.vectors, k)
        return "\n".join(self.texts[i] for i in idxs)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd probe && python3 -m pytest test_embed.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/embed.py probe/test_embed.py
git commit -m "feat(embed): EmbeddingIndex over types.jsonl — cosine top-k + (model,corpus) cache"
```

---

## Task 3: Build-embeddings CLI + script

**Files:**
- Modify: `probe/embed.py` (add `build_cli` + `__main__`)
- Create: `scripts/build-embeddings.sh`
- Test: `probe/test_embed.py`

**Interfaces:**
- Consumes: `load_premises`, `EmbeddingIndex`, `mistral_embed` (Tasks 1-2); `scout_index.default_index_dir`.
- Produces: cache file `index/embeddings-<model>.json`; `cache_path(index_dir, model) -> str`; `build_cli(argv) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# append to probe/test_embed.py
def test_cache_path_is_per_model_under_index_dir():
    assert embed.cache_path("index", "mistral-embed") == "index/embeddings-mistral-embed.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd probe && python3 -m pytest test_embed.py::test_cache_path_is_per_model_under_index_dir -v`
Expected: FAIL with `AttributeError: module 'embed' has no attribute 'cache_path'`.

- [ ] **Step 3: Write minimal implementation**

```python
# append to probe/embed.py
import argparse


def cache_path(index_dir: str, model: str) -> str:
    return os.path.join(index_dir, f"embeddings-{model}.json")


def build_cli(argv=None) -> int:
    """Embed index/types.jsonl and write the vector cache. Host-side HTTP — NO
    Lean process, so it is orthogonal to the daemon slot."""
    ap = argparse.ArgumentParser(prog="embed build")
    ap.add_argument("--index-dir", default=None)
    ap.add_argument("--model", default=DEFAULT_EMBED_MODEL)
    ap.add_argument("--batch", type=int, default=256)
    args = ap.parse_args(argv)

    from scout_index import default_index_dir
    index_dir = args.index_dir or default_index_dir()
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2
    premises = load_premises(index_dir)
    if not premises:
        print(f"no types.jsonl under {index_dir}", file=sys.stderr)
        return 1

    idx = EmbeddingIndex(premises, model=args.model)

    def embed_fn(texts):
        out: list[list[float]] = []
        for i in range(0, len(texts), args.batch):
            out.extend(mistral_embed(texts[i:i + args.batch],
                                     api_key=api_key, model=args.model))
            print(f"  embedded {min(i + args.batch, len(texts))}/{len(texts)}",
                  file=sys.stderr)
        return out

    idx.build(embed_fn)
    out_path = cache_path(index_dir, args.model)
    idx.save(out_path)
    print(f"wrote {out_path} ({len(premises)} premises, model={args.model})")
    return 0


if __name__ == "__main__":
    sys.exit(build_cli())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd probe && python3 -m pytest test_embed.py::test_cache_path_is_per_model_under_index_dir -v`
Expected: PASS.

- [ ] **Step 5: Write the build script**

```bash
# scripts/build-embeddings.sh
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
```

- [ ] **Step 6: Make it executable + commit**

```bash
cd /home/rapha/code/formal-foundry
chmod +x scripts/build-embeddings.sh
git add probe/embed.py probe/test_embed.py scripts/build-embeddings.sh
git commit -m "feat(embed): build-embeddings CLI + script (embed committed types.jsonl)"
```

---

## Task 4: Config fields + backend-selected retrieve_fn

**Files:**
- Modify: `probe/pipeline_lib.py:61-90` (config), `probe/embed.py` (factory)
- Test: `probe/test_embed.py`, `probe/test_autoformalize.py`

**Interfaces:**
- Consumes: `EmbeddingIndex.load` / `cache_path` / `load_premises` (Tasks 2-3).
- Produces: `make_embedding_retrieve_fn(index, k, embed_fn) -> Callable[[str], str]`; config fields `retrieval_backend: str = "embedding"`, `retrieval_k: int = 8`, `embed_model: str = "mistral-embed"`, `autop: bool = True`.

- [ ] **Step 1: Write the failing tests**

```python
# append to probe/test_embed.py
def test_make_embedding_retrieve_fn_is_str_to_str():
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]

    def fake_embed(texts):
        return [[1.0, 0.0] for _ in texts]

    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(fake_embed)
    fn = embed.make_embedding_retrieve_fn(idx, k=1, embed_fn=fake_embed)
    out = fn("MathFin.zcb")
    assert isinstance(out, str) and "MathFin.zcb" in out
```

```python
# append to probe/test_autoformalize.py
def test_autoformalize_config_retrieval_backend_defaults():
    cfg = pl.AutoformalizeConfig.load(None)
    assert cfg.retrieval_backend == "embedding"
    assert cfg.retrieval_k == 8
    assert cfg.embed_model == "mistral-embed"
    assert cfg.autop is True


def test_autoformalize_config_retrieval_backend_reads_toml(tmp_path):
    toml = tmp_path / "pipeline.toml"
    toml.write_text('[autoformalize]\nretrieval_backend = "loogle"\nautop = false\n')
    cfg = pl.AutoformalizeConfig.load(str(toml))
    assert cfg.retrieval_backend == "loogle"
    assert cfg.autop is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd probe && python3 -m pytest test_embed.py::test_make_embedding_retrieve_fn_is_str_to_str test_autoformalize.py -k retrieval_backend -v`
Expected: FAIL (`make_embedding_retrieve_fn` missing; config has no `retrieval_backend`).

- [ ] **Step 3: Write minimal implementation**

Add to `probe/embed.py`:
```python
def make_embedding_retrieve_fn(index: "EmbeddingIndex", k: int, embed_fn):
    """A drop-in `retrieve_fn(query: str) -> str` over `index` — same shape as
    loogle_candidates, but ranks the WHOLE MathFin corpus by cosine similarity."""
    def retrieve(query: str) -> str:
        return index.retrieve(query, k, embed_fn)
    return retrieve
```

Add the four fields to `AutoformalizeConfig` in `probe/pipeline_lib.py` (after line 90, before `@staticmethod load`):
```python
    # embedding premise retrieval (pin-accurate types.jsonl) with loogle fallback.
    retrieval_backend: str = "embedding"   # "embedding" | "loogle"
    retrieval_k: int = 8                    # top-k premises surfaced per query
    embed_model: str = "mistral-embed"      # Mistral /v1/embeddings model id
    # cheap prove probe: try a fixed tactic menu as whole-proof scripts (scout).
    autop: bool = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd probe && python3 -m pytest test_embed.py test_autoformalize.py -k "retrieve or retrieval_backend or config" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/embed.py probe/pipeline_lib.py probe/test_embed.py probe/test_autoformalize.py
git commit -m "feat(embed): retrieve_fn factory + retrieval_backend/retrieval_k/embed_model/autop config"
```

---

## Task 5: Proactive premise injection into formalize

**Files:**
- Modify: `probe/autoformalize.py:517-577` (`formalize_with_repair`), `:754-791` (`refill`)
- Test: `probe/test_autoformalize.py`

**Interfaces:**
- Consumes: `retrieve_fn` (embedding, `str -> str`) from Task 4.
- Produces: `formalize_with_repair(..., proactive_premises: str = "")`; `refill(..., proactive_fn=None)` — `proactive_fn(statement: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# append to probe/test_autoformalize.py
def test_formalize_injects_proactive_premises_into_first_message():
    captured = {}

    def chat(msgs):
        captured["msgs"] = msgs
        return ("```lean\ntheorem t : True := by sorry\n```", 10)

    intent = {"module_name": "M", "benchmark_id": "b", "statement": "True", "docstring": ""}
    af.formalize_with_repair(
        intent, "GROUNDING", issue={"number": 1, "name": "n", "domain": "d"},
        chat_fn=chat, check_fn=lambda t: {"errors": [], "sorry_count": 1},
        emit_fn=lambda i, s, m: ("LEAN", {"id": "x"}, None),
        rounds=1, proactive_premises="MathFin.zcb : ℝ → ℝ")
    blob = "\n".join(m["content"] for m in captured["msgs"])
    assert "MathFin.zcb : ℝ → ℝ" in blob
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd probe && python3 -m pytest test_autoformalize.py::test_formalize_injects_proactive_premises_into_first_message -v`
Expected: FAIL with `TypeError: formalize_with_repair() got an unexpected keyword argument 'proactive_premises'`.

- [ ] **Step 3: Write minimal implementation**

In `probe/autoformalize.py`, change the `formalize_with_repair` signature (line 517-519) to add the param, and fold it into the grounding:
```python
def formalize_with_repair(intent: dict, grounding: str, *, issue: dict, chat_fn, check_fn,
                          emit_fn, rounds: int = 3, retrieve_fn=None,
                          token_budget: int = 40_000, proactive_premises: str = "",
                          log=lambda m: None) -> dict:
```
Immediately after the docstring, before `meta = {...}` (line 527), fold proactive premises into the grounding used to seed the messages:
```python
    if proactive_premises:
        grounding = (grounding + "\n\n── LIKELY-RELEVANT PREMISES (rank by cosine; "
                     "verify they elaborate under our pin) ──\n" + proactive_premises)
```
(The existing `messages = formalize_messages(intent, grounding)` at line 529 then carries it into the first user message.)

In `refill` (line 787), pass proactive premises computed from the intent statement. Change the block at lines 785-791 to:
```python
            intent = di["intent"]

            proactive = proactive_fn(intent["statement"]) if proactive_fn else ""
            fr = formalize_with_repair(intent, ctx, issue=issue, chat_fn=formalize_fn,
                                       check_fn=check_fn, emit_fn=emit_target_files,
                                       rounds=formalize_rounds, retrieve_fn=retrieve_fn,
                                       token_budget=formalize_token_budget,
                                       proactive_premises=proactive,
                                       log=lambda m: log(f"#{n} formalize {m}"))
```
Add `proactive_fn=None` to the `refill` signature (line 754-758 block), e.g. after `retrieve_fn=None,`:
```python
           formalize_token_budget: int = 40_000, formalize_fn=None, retrieve_fn=None,
           proactive_fn=None,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd probe && python3 -m pytest test_autoformalize.py -k "proactive or formalize or refill" -v`
Expected: PASS (new test passes; existing formalize/refill tests still green — `proactive_fn` defaults to `None`, `proactive_premises` to `""`).

- [ ] **Step 5: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/autoformalize.py probe/test_autoformalize.py
git commit -m "feat(autoform): proactive premise injection into the initial formalize grounding"
```

---

## Task 6: autop prove probe (isolated)

**Files:**
- Create: `probe/autop.py`
- Test: `probe/test_autop.py`

**Interfaces:**
- Consumes: a `check_fn(lean_text: str) -> {"success": bool, "sorry_count": int, "errors": [...]}` (the daemon check shape used by `run_target`).
- Produces: `AUTOP_MENU: tuple[str, ...]`; `autop_candidate(statement: str, tactic: str) -> str`; `autop_prove(statement, *, check_fn, menu=AUTOP_MENU) -> dict | None` returning `{"tactic", "proof"}`.

- [ ] **Step 1: Write the failing tests**

```python
# probe/test_autop.py
"""Pure tests for the autop prove probe — injected check_fn, no daemon."""
from __future__ import annotations

import autop


def test_autop_candidate_replaces_by_sorry_with_tactic():
    stmt = "theorem t (x : ℝ) : x = x := by sorry"
    assert autop.autop_candidate(stmt, "nlinarith") == "theorem t (x : ℝ) : x = x := by nlinarith"


def test_autop_prove_returns_first_tactic_that_closes():
    calls = []

    def check(text):
        calls.append(text)
        ok = text.endswith("by nlinarith")
        return {"success": ok, "sorry_count": 0 if ok else 1, "errors": []}

    res = autop.autop_prove("theorem t : True := by sorry", check_fn=check,
                            menu=("simp", "nlinarith", "aesop"))
    assert res == {"tactic": "nlinarith",
                   "proof": "theorem t : True := by nlinarith"}
    assert len(calls) == 2   # stops at nlinarith, never tries aesop


def test_autop_prove_rejects_success_with_residual_sorry():
    # a tactic that "succeeds" but leaves a sorry is NOT a close
    def check(text):
        return {"success": True, "sorry_count": 1, "errors": []}

    assert autop.autop_prove("theorem t : True := by sorry", check_fn=check,
                             menu=("simp",)) is None


def test_autop_prove_returns_none_when_all_fail():
    def check(text):
        return {"success": False, "sorry_count": 1, "errors": ["boom"]}

    assert autop.autop_prove("theorem t : True := by sorry", check_fn=check) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd probe && python3 -m pytest test_autop.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'autop'`.

- [ ] **Step 3: Write minimal implementation**

```python
# probe/autop.py
"""The cheap prove probe: try a fixed menu of Lean's strong closing tactics as
whole-proof scripts, verified through the daemon. A win is a SCOUT (a lead to the
conceptually-right proof), never the merged author proof — the caller scout-tags
it and opens a DRAFT PR. Design inspiration: ulam's autop fallbacks (no code
vendored). See the 2026-07-16 spec.
"""
from __future__ import annotations

# Lean/Mathlib built-in closers, cheapest/most-common first. `grind` last (it is
# the heaviest search). These mirror ulam's autop set adapted to our toolkit.
AUTOP_MENU: tuple[str, ...] = (
    "simp", "norm_num", "ring_nf", "linarith", "nlinarith", "aesop", "grind",
)


def autop_candidate(statement: str, tactic: str) -> str:
    """The staged stub with its `:= by sorry` replaced by `:= by <tactic>`. The
    staged stub ends in exactly one `by sorry` (the target), so a plain replace
    is unambiguous."""
    return statement.replace("by sorry", f"by {tactic}")


def autop_prove(statement: str, *, check_fn, menu=AUTOP_MENU) -> dict | None:
    """First menu tactic whose whole-proof script elaborates with 0 sorries, as
    `{"tactic", "proof"}`; None if none close. Values gate: the caller must treat
    a result as a SCOUT (never a silent merge)."""
    for tactic in menu:
        cand = autop_candidate(statement, tactic)
        res = check_fn(cand)
        if res.get("success") and res.get("sorry_count", 1) == 0:
            return {"tactic": tactic, "proof": cand}
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd probe && python3 -m pytest test_autop.py -v`
Expected: PASS (all 4).

- [ ] **Step 5: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/autop.py probe/test_autop.py
git commit -m "feat(autop): cheap tactic-menu prove probe (scout, injected check_fn)"
```

---

## Task 7: Wire retrieval + autop + scout into the prove path

**Files:**
- Modify: `probe/probe.py:296-322` (the `prove` loop)
- Modify: `probe/autoformalize.py:936-948` (main wiring: backend-selected `retrieve_fn` + `proactive_fn`)
- Modify: `scripts/open-pr.sh` (DRAFT + `scout-proof` label on scout targets)

**Interfaces:**
- Consumes: `embed.load_premises/cache_path/EmbeddingIndex/make_embedding_retrieve_fn`, `autop.autop_prove`, `EmbeddingIndex.retrieve` (Tasks 2-6).
- Produces: prove `summary` gains `"autop"` (tactic or None) + `"scout"` (bool); scout targets write `_winning_candidate` (autop proof) + `_proof_source`.

- [ ] **Step 1: Write the failing test (autoformalize backend selection)**

```python
# append to probe/test_autoformalize.py
def test_build_retrieve_fns_selects_loogle_when_configured(monkeypatch):
    # backend "loogle" ⇒ reactive loogle fn, no proactive fn (loogle is name-only)
    r, p = af.build_retrieve_fns(backend="loogle", main_repo="/x", index_dir="/no/index",
                                 k=8, embed_model="mistral-embed", api_key="k")
    assert r is not None and p is None


def test_build_retrieve_fns_falls_open_to_loogle_when_index_absent():
    # backend "embedding" but no cache present ⇒ degrade to loogle, no proactive
    r, p = af.build_retrieve_fns(backend="embedding", main_repo="/x",
                                 index_dir="/no/index", k=8,
                                 embed_model="mistral-embed", api_key="k")
    assert r is not None and p is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd probe && python3 -m pytest test_autoformalize.py -k build_retrieve_fns -v`
Expected: FAIL with `AttributeError: module 'autoformalize' has no attribute 'build_retrieve_fns'`.

- [ ] **Step 3: Write minimal implementation**

Add a factory to `probe/autoformalize.py` (near the main wiring, before `def main`). Import at top (with the other `from probe import ...` / local imports, near line 24-27):
```python
import embed as _embed
from autop import autop_prove  # noqa: F401  (re-exported for probe.py prove path)
```
Factory:
```python
def build_retrieve_fns(*, backend, main_repo, index_dir, k, embed_model, api_key):
    """(reactive_retrieve_fn, proactive_fn). Embedding backend ranks the whole
    MathFin corpus; proactive_fn retrieves on the intent STATEMENT. Falls open to
    loogle (reactive only) when the embedding cache is absent."""
    loogle_fn = lambda nm: loogle_candidates(nm, main_repo=main_repo)  # noqa: E731
    if backend != "embedding":
        return loogle_fn, None
    premises = _embed.load_premises(index_dir)
    cache = _embed.cache_path(index_dir, embed_model)
    idx = _embed.EmbeddingIndex.load(cache, premises, embed_model) if premises else None
    if idx is None:
        return loogle_fn, None   # fails-open — no index/cache ⇒ loogle
    embed_fn = lambda texts: _embed.mistral_embed(texts, api_key=api_key, model=embed_model)  # noqa: E731
    reactive = _embed.make_embedding_retrieve_fn(idx, k, embed_fn)
    proactive = lambda stmt: idx.retrieve(stmt, k, embed_fn)  # noqa: E731
    return reactive, proactive
```
Replace line 940 (`retrieve_fn = (lambda nm: ...) if retrieval else None`) with:
```python
    from scout_index import default_index_dir
    index_dir = default_index_dir()
    if retrieval:
        retrieve_fn, proactive_fn = build_retrieve_fns(
            backend=cfg.retrieval_backend, main_repo=args.main_repo, index_dir=index_dir,
            k=cfg.retrieval_k, embed_model=cfg.embed_model, api_key=api_key)
    else:
        retrieve_fn, proactive_fn = None, None
```
Add `proactive_fn=proactive_fn,` to the `refill(...)` call (after `retrieve_fn=retrieve_fn,` at line 946). (`cfg` is already in scope in `main`; confirm with a grep — it is loaded above where `retrieval = pick(args.retrieval, cfg.retrieval)` at line 906.)

- [ ] **Step 4: Wire the prove loop in `probe/probe.py`**

Add imports near the top of `probe/probe.py` (with the existing imports):
```python
import embed as _embed
from autop import autop_prove
from scout_index import default_index_dir
```
Add prove-subcommand args (after line 271, before `args = ap.parse_args()`):
```python
    p.add_argument("--retrieval-backend", default="embedding", choices=["embedding", "loogle"])
    p.add_argument("--retrieval-k", type=int, default=8)
    p.add_argument("--embed-model", default="mistral-embed")
    p.add_argument("--autop", dest="autop", action="store_true", default=True)
    p.add_argument("--no-autop", dest="autop", action="store_false")
```
Before the `for target` loop (after line 294), build the embedding retriever once:
```python
    index_dir = default_index_dir()
    _premises = _embed.load_premises(index_dir)
    _eidx = (_embed.EmbeddingIndex.load(_embed.cache_path(index_dir, args.embed_model),
                                        _premises, args.embed_model)
             if (args.retrieval_backend == "embedding" and _premises) else None)

    def _retrieve_premises(statement):
        if _eidx is None:
            return ""
        ef = lambda t: _embed.mistral_embed(t, api_key=api_key, model=args.embed_model)  # noqa: E731
        return _eidx.retrieve(statement, args.retrieval_k, ef)
```
Replace the body from line 303 (`context_pack = ...`) through the winning-candidate write (line 321) with:
```python
        context_pack = extract_signatures(args.main_repo, pointers) if pointers else ""
        premises = _retrieve_premises(target["statement"])
        if premises:
            context_pack += ("\n── LIKELY-RELEVANT PREMISES (cosine-ranked; consume, "
                             "don't reprove) ──\n" + premises)
        print(f"[{target['id']}] budget={args.budget} pointers={len(pointers)} "
              f"premises={'y' if premises else 'n'} autop={'y' if args.autop else 'n'} …",
              flush=True)

        # autop probe (evidence + scout safety net); leanstral still runs for an
        # AUTHOR proof — autop never reduces leanstral effort, only rescues misses.
        autop_res = autop_prove(target["statement"], check_fn=daemon_check) if args.autop else None

        summary = run_target(target, budget=args.budget,
                             max_rounds=args.max_rounds, chat_fn=chat_fn,
                             check_fn=daemon_check,
                             log_fn=lambda r: append_jsonl(attempts_log, r),
                             system_prompt=system_prompt, context_pack=context_pack,
                             fanout=args.fanout, repair_rounds=args.repair_rounds)
        summary["model"] = args.model
        summary["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        summary["autop"] = autop_res["tactic"] if autop_res else None   # prove-wall evidence
        summary["scout"] = False
        if summary["outcome"] != "pass" and autop_res:
            # leanstral missed but a cheap tactic closes it → SCOUT rescue (draft PR)
            target["_winning_candidate"] = autop_res["proof"]
            target["_proof_source"] = f"autop-{autop_res['tactic']}"
            summary["outcome"] = "pass_scout"
            summary["scout"] = True
        append_jsonl(summary_log, summary)
        print(f"  -> {summary['outcome']} rounds={summary['rounds']} "
              f"tokens={summary['tokens']} autop={summary['autop']}", flush=True)
        if "_winning_candidate" in target:
            win_path = os.path.join(run_dir, f"{args.run_tag}-{target['id']}.lean")
            with open(win_path, "w") as f:
                f.write(target["_winning_candidate"])
            if target.get("_proof_source"):
                with open(win_path + ".scout", "w") as f:
                    f.write(target["_proof_source"])
```

- [ ] **Step 5: Wire the scout draft-PR in `scripts/open-pr.sh`**

Grep for where the PR is created:
```bash
cd /home/rapha/code/formal-foundry && grep -n "gh pr create\|--id\|CAND\|run_tag\|run-tag" scripts/open-pr.sh | head
```
At the top of `open-pr.sh` (after it computes the candidate path `$CAND` for `--id`), detect the scout sidecar and set PR flags:
```bash
# scout proofs (autop-closed) open as DRAFT + labeled — a lead to refactor, never
# a silent merge (values gate). Author (leanstral) proofs open as normal PRs.
PR_FLAGS=()
if [ -f "${CAND}.scout" ]; then
  PR_FLAGS+=(--draft --label scout-proof)
  SCOUT_NOTE=$'\n\n> scout proof: closed by `'"$(cat "${CAND}.scout")"$'`. needs refactor to the conceptually-right proof before merge.'
else
  SCOUT_NOTE=""
fi
```
Then add `"${PR_FLAGS[@]}"` to the `gh pr create` invocation and append `$SCOUT_NOTE` to the `--body`. (Adapt to the exact `gh pr create` line the grep surfaced — keep the existing title/body, just add the flags + note.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd probe && python3 -m pytest test_autoformalize.py -k build_retrieve_fns -v && python3 -m pytest -q`
Expected: the two `build_retrieve_fns` tests PASS; the full foundry suite stays green.

- [ ] **Step 7: Commit**

```bash
cd /home/rapha/code/formal-foundry
git add probe/autoformalize.py probe/probe.py scripts/open-pr.sh probe/test_autoformalize.py
git commit -m "feat(prove): embedding retrieval-into-prove + autop scout rescue (draft PR, never silent merge)"
```

---

## Task 8: Config surface + live smoke + push

**Files:**
- Modify: `pipeline.toml` (`[autoformalize]`)
- No new tests (integration/live smoke only)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Surface the new keys in `pipeline.toml`**

Add under the existing `[autoformalize]` block:
```toml
retrieval_backend = "embedding"   # "embedding" (types.jsonl cosine) | "loogle"
retrieval_k = 8
embed_model = "mistral-embed"
autop = true                      # cheap tactic-menu prove probe (scout)
```

- [ ] **Step 2: Full unit suite green**

Run: `cd probe && python3 -m pytest -q`
Expected: PASS (no failures, no errors).

- [ ] **Step 3: Build the embedding cache (live, host-side — NO daemon needed)**

Run:
```bash
cd /home/rapha/code/formal-foundry && MISTRAL_API_KEY=$MISTRAL_API_KEY ./scripts/build-embeddings.sh
```
Expected: `wrote index/embeddings-mistral-embed.json (2785 premises, model=mistral-embed)`.

- [ ] **Step 4: Live retrieval smoke**

Run:
```bash
cd probe && python3 -c "
import os, embed
from scout_index import default_index_dir
d = default_index_dir()
prem = embed.load_premises(d)
idx = embed.EmbeddingIndex.load(embed.cache_path(d, 'mistral-embed'), prem, 'mistral-embed')
ef = lambda t: embed.mistral_embed(t, api_key=os.environ['MISTRAL_API_KEY'], model='mistral-embed')
print(idx.retrieve('zero coupon bond price discount factor', k=5, embed_fn=ef))
"
```
Expected: 5 `name : type` lines; bond/discount-related MathFin premises rank at the top (sanity that cosine ranking is meaningful, not random).

- [ ] **Step 5: Live autop smoke (daemon up, ONE Lean process)**

Bring the daemon up per the main repo's workflow, then:
```bash
cd probe && python3 -c "
import autop
from probe import daemon_check
stmt = 'import Mathlib\ntheorem autop_smoke (x : ℝ) : x + 0 = x := by sorry'
print(autop.autop_prove(stmt, check_fn=daemon_check))
"
```
Expected: `{'tactic': 'simp', 'proof': '...by simp'}` (or another menu tactic) — proves the probe closes a trivial goal through the daemon. Take the daemon down afterward.

- [ ] **Step 6: Decide vector-cache commit vs CI (open question from the spec)**

Check the cache size:
```bash
ls -lh /home/rapha/code/formal-foundry/index/embeddings-mistral-embed.json
```
If ≲ 30 MB, `git add` it (deterministic per pin, keeps ticks self-contained — like `types.jsonl` is committed). If larger, add `index/embeddings-*.json` to `.gitignore` and add a CI step running `scripts/build-embeddings.sh` before the prove step instead. Record the decision in the spec's Risks section.

- [ ] **Step 7: Commit + push**

```bash
cd /home/rapha/code/formal-foundry
git add pipeline.toml   # + index/embeddings-mistral-embed.json IF committing (Step 6)
git commit -m "feat(pipeline): enable embedding retrieval + autop probe in [autoformalize]"
git push origin main
```

- [ ] **Step 8: Update the spec status**

Set the spec header `Status:` to `IMPLEMENTED (2026-07-16)` and note the vector-cache decision from Step 6. Commit:
```bash
git add docs/superpowers/specs/2026-07-16-embedding-retrieval-prove-probe-design.md
git commit -m "docs(specs): mark embedding-retrieval + prove-probe implemented"
git push origin main
```

---

## Self-Review

**Spec coverage:**
- Corpus = committed `types.jsonl` → Tasks 2-3 (`load_premises`, build script). ✓
- `mistral_embed` stdlib mirror → Task 1. ✓
- `EmbeddingIndex` cosine + (model, sha256) cache → Task 2. ✓
- `embedding_retrieve_fn` drop-in at reactive seam → Task 4 + Task 7 wiring. ✓
- Proactive top-k into initial formalize grounding → Task 5. ✓
- Loogle fallback / fails-open → Task 4 config + Task 7 `build_retrieve_fns`. ✓
- Autop menu as whole-proof scripts via existing daemon → Task 6 + Task 7. ✓
- Retrieval-into-prove → Task 7 (`context_pack` enrichment). ✓
- JSONL instrumentation of prove outcome → Task 7 (`summary["autop"]`). ✓
- Scout tag + never auto-merged (draft PR + label) → Task 7 (`open-pr.sh`). ✓
- MathFin-only corpus scope (loogle kept for Mathlib) → Task 4/7 keep loogle. ✓
- Memory doctrine (no 2nd Lean process) → build is host-side (Task 3); autop reuses the one daemon (Task 7). ✓
- Config surface → Task 4 + Task 8. ✓
- Deferred (B2 search, self-consistency) → not in any task, by design. ✓

**Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N" — every code step has complete code. Task 7 Step 5 (`open-pr.sh`) instructs a grep then an exact snippet to add, because the existing `gh pr create` line must be matched in place; the snippet to add is fully written. ✓

**Type consistency:** `mistral_embed(list[str]) -> list[list[float]]` used consistently by `EmbeddingIndex.build`/`.retrieve` (embed_fn), Task 3 CLI, Task 7 wiring. `retrieve_fn: str -> str` matches the existing `loogle_candidates` shape (`autoformalize.py:485`) and the `retrieve_fn` call site (`:571`). `check_fn` return `{"success", "sorry_count", "errors"}` matches `run_target`'s usage (`probe.py:210`). `autop_prove -> {"tactic","proof"} | None` consumed in Task 7 exactly as produced in Task 6. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-embedding-retrieval-prove-probe.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
