"""Embedding premise retrieval over the pin-accurate lean_scout `types.jsonl`.

stdlib-only (mirrors scout_index.py / probe.py): urllib for the Mistral
`/v1/embeddings` call, pure-Python cosine ranking. Design:
docs/superpowers/specs/2026-07-16-embedding-retrieval-prove-probe-design.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
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


# Lean-internal / auto-generated names are unusable as retrieval candidates (the
# model can't cite `_private.…`, `.casesOn`, an auto-gen simp lemma, …) — and they
# are ~45% of the raw types.jsonl, so filtering them sharpens the top-k the model sees.
_PREMISE_INTERNAL_INFIX = (
    "._simp", "._proof_", ".match_", "._sunfold", "._eq_", "._cstage",
    ".congr_simp", "._flat_ctor",
)
_PREMISE_INTERNAL_SUFFIX = (
    ".casesOn", ".recOn", ".rec", ".recAux", ".brecOn", ".below", ".ibelow",
    ".noConfusion", ".noConfusionType", ".ind", ".sizeOf", ".sizeOf_spec",
    ".injEq", ".eq_def", ".mk", ".ofNat", ".toCtorIdx",
)
_PREMISE_EQNUM = re.compile(r"\.eq_\d+$")


def _is_usable_premise(name: str) -> bool:
    """False for a Lean-internal / auto-generated name (private decl, simp/proof/
    match internal, structure eliminator, numbered equation lemma) — none of which
    the model can reference. True for a real, citable MathFin lemma or def."""
    if not name or name.startswith("_private."):
        return False
    if any(m in name for m in _PREMISE_INTERNAL_INFIX):
        return False
    if any(name.endswith(s) for s in _PREMISE_INTERNAL_SUFFIX):
        return False
    return not _PREMISE_EQNUM.search(name)


def load_premises(index_dir: str) -> list[dict]:
    """The SIGNAL types.jsonl records (name/module/type/docString): real, citable
    MathFin decls only. Two combined filters — Lean's own `allowCompletion` flag
    (drops `._f` / `.ctorIdx` / auto-gen internals it authoritatively marks) AND the
    `_is_usable_premise` name guard (drops what `allowCompletion` still lets through:
    `_private.` mangled names, `.eq_N`, `.congr_simp`, structure constructors). ~45%
    of the raw index is such noise. [] if absent — callers then fall back to loogle."""
    path = os.path.join(index_dir, "types.jsonl")
    recs: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                # `is not False` (not `is True`) so a future field-less index degrades
                # to the name guard alone rather than dropping everything.
                if rec.get("allowCompletion") is not False and _is_usable_premise(rec.get("name", "")):
                    recs.append(rec)
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
        idx.vectors = blob.get("vectors")
        if idx.vectors is None:
            return None
        return idx

    def retrieve(self, query: str, k: int, embed_fn) -> str:
        """Top-k premises for `query` as `name : type` lines ('' if not built)."""
        if not self.vectors:
            return ""
        qv = embed_fn([query])[0]
        idxs = top_k(qv, self.vectors, k)
        return "\n".join(self.texts[i] for i in idxs)


def cache_path(index_dir: str, model: str) -> str:
    return os.path.join(index_dir, f"embeddings-{model}.json")


def make_embedding_retrieve_fn(index: "EmbeddingIndex", k: int, embed_fn):
    """A drop-in `retrieve_fn(query: str) -> str` over `index` — same shape as
    loogle_candidates, but ranks the WHOLE MathFin corpus by cosine similarity."""
    def retrieve(query: str) -> str:
        return index.retrieve(query, k, embed_fn)
    return retrieve


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
