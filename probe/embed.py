"""Embedding premise retrieval over the pin-accurate lean_scout `types.jsonl`.

Since 2026-08-14 that index carries the library AND the Mathlib neighbourhoods its
proofs actually reach (`index_filter.py`), so this is the drafter's
first pin-accurate semantic view of Mathlib — previously its only Mathlib
channel was an off-pin public loogle. The corpus is ~an order of magnitude
bigger as a result, which is why the vectors live in a binary sidecar rather
than inline JSON (see `vectors_path`).

stdlib-only (mirrors scout_index.py / probe.py): urllib for the Mistral
`/v1/embeddings` call, pure-Python cosine ranking. Design:
docs/superpowers/specs/2026-07-16-embedding-retrieval-prove-probe-design.md.
"""
from __future__ import annotations

import argparse
import array
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
    the model can reference. True for a real, citable library lemma or def (ours or Mathlib)."""
    if not name or name.startswith("_private."):
        return False
    if any(m in name for m in _PREMISE_INTERNAL_INFIX):
        return False
    if any(name.endswith(s) for s in _PREMISE_INTERNAL_SUFFIX):
        return False
    return not _PREMISE_EQNUM.search(name)


def load_premises(index_dir: str) -> list[dict]:
    """The SIGNAL types.jsonl records (name/module/type/docString): real, citable
    own-namespace decls only. Two combined filters — Lean's own `allowCompletion` flag
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


def top_k_flat(query_vec: list[float], flat: "array.array", dim: int, k: int,
               norms: list[float] | None = None) -> list[int]:
    """`top_k` over a flat float32 store — same ranking, without materializing
    the matrix as Python lists (see `vectors_path` for why that matters)."""
    if not dim or not len(flat):
        return []
    nq = math.sqrt(sum(x * x for x in query_vec))
    if nq == 0.0:
        return []
    scored: list[tuple[float, int]] = []
    for i in range(len(flat) // dim):
        row = flat[i * dim:(i + 1) * dim]          # C-level slice of the array
        nr = norms[i] if norms is not None else math.sqrt(sum(y * y for y in row))
        s = 0.0 if nr == 0.0 else sum(x * y for x, y in zip(query_vec, row)) / (nq * nr)
        scored.append((s, i))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [i for _, i in scored[:k]]


def vectors_path(cache_file: str) -> str:
    """Binary sidecar holding the vectors of `cache_file` as raw float32.

    Kept OUT of the JSON now that the corpus spans Mathlib too. Measured on this
    box at 1024 dims: inline JSON floats run ~1.0 GB on disk and ~8s to parse at
    50k premises, and materializing them as Python float objects costs ~1.2 GB
    of RAM — on a box whose whole doctrine is that it has no headroom to spare.
    The same vectors are ~205 MB as one `array('f')`, loaded in one read."""
    base = cache_file[:-5] if cache_file.endswith(".json") else cache_file
    return base + ".f32"


class EmbeddingIndex:
    """Vectors for a premise corpus + cosine top-k retrieval. Build once per pin
    (vectors cached to disk keyed by (model, corpus_hash)); query embeds one text
    and ranks locally.

    Vectors are held flat as float32. The precision loss is well under the gap
    between adjacent cosine scores, and it halves both the file and the resident
    set relative to float64."""

    def __init__(self, premises: list[dict], *, model: str):
        self.premises = premises
        self.model = model
        self.texts = [premise_text(p) for p in premises]
        self.hash = corpus_hash(self.texts, model)
        self._flat: "array.array | None" = None
        self._dim = 0
        self._norms: list[float] | None = None

    # `vectors` stays a plain attribute to callers; the store underneath is flat.
    @property
    def vectors(self) -> list[list[float]] | None:
        """Row view, materialized on demand. Prefer `retrieve` on a large corpus
        — this rebuilds the whole matrix as Python lists."""
        if self._flat is None:
            return None
        d = self._dim
        if not d:
            return []
        return [list(self._flat[i * d:(i + 1) * d]) for i in range(len(self._flat) // d)]

    @vectors.setter
    def vectors(self, rows: list[list[float]] | None) -> None:
        if rows is None:
            self._flat, self._dim, self._norms = None, 0, None
            return
        self._dim = len(rows[0]) if rows else 0
        self._flat = array.array("f", (x for row in rows for x in row))
        self._norms = None

    def _row_norms(self) -> list[float]:
        """Row norms, computed once — halves the per-query scan."""
        if self._norms is None:
            d, flat = self._dim, self._flat
            self._norms = [] if (not d or flat is None) else [
                math.sqrt(sum(y * y for y in flat[i * d:(i + 1) * d]))
                for i in range(len(flat) // d)]
        return self._norms

    def build(self, embed_fn) -> "EmbeddingIndex":
        """Embed the corpus. `embed_fn(list[str]) -> list[list[float]]`."""
        self.vectors = embed_fn(self.texts)
        return self

    def save(self, path: str) -> None:
        """Metadata to `path`, vectors to its `.f32` sidecar."""
        if self._flat is None:
            raise ValueError("nothing to save — build() first")
        with open(vectors_path(path), "wb") as f:
            self._flat.tofile(f)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"model": self.model, "corpus_hash": self.hash,
                       "dim": self._dim, "count": len(self._flat) // self._dim
                       if self._dim else 0,
                       "byteorder": sys.byteorder,
                       "vectors_file": os.path.basename(vectors_path(path))}, f)

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
        inline = blob.get("vectors")
        if inline is not None:                     # pre-sidecar cache; still valid
            idx.vectors = inline
            return idx
        dim, count = blob.get("dim"), blob.get("count")
        if not dim or not count:
            return None
        # float32 is written in native order; a cache moved across architectures
        # would decode to noise, so treat a mismatch as stale rather than trust it.
        if blob.get("byteorder") != sys.byteorder:
            return None
        flat = array.array("f")
        try:
            with open(vectors_path(path), "rb") as f:
                flat.fromfile(f, dim * count)
        except (OSError, EOFError, ValueError):
            return None
        idx._flat, idx._dim = flat, dim
        return idx

    def retrieve(self, query: str, k: int, embed_fn) -> str:
        """Top-k premises for `query` as `name : type` lines ('' if not built)."""
        if self._flat is None or not self._dim:
            return ""
        qv = embed_fn([query])[0]
        idxs = top_k_flat(qv, self._flat, self._dim, k, norms=self._row_norms())
        return "\n".join(self.texts[i] for i in idxs)


def corpus_composition(premises: list[dict]) -> dict[str, int]:
    """{top-level module namespace: count} — what an embed run would spend on."""
    out: dict[str, int] = {}
    for p in premises:
        ns = (p.get("module") or "?").split(".", 1)[0] or "?"
        out[ns] = out.get(ns, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def cache_path(index_dir: str, model: str) -> str:
    return os.path.join(index_dir, f"embeddings-{model}.json")


def make_embedding_retrieve_fn(index: "EmbeddingIndex", k: int, embed_fn):
    """A drop-in `retrieve_fn(query: str) -> str` over `index` — same shape as
    loogle_candidates, but ranks the whole premise corpus by cosine similarity:
    ours plus the Mathlib neighbourhoods our proofs reach, unlike loogle
    pin-accurate."""
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
    ap.add_argument("--max-premises", type=int, default=None,
                    help="cap the corpus (own-namespace records are kept first)")
    ap.add_argument("--domain", default=None,
                    help="domain pack whose own namespaces sort first under --max-premises")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the corpus composition and exit without calling the API")
    args = ap.parse_args(argv)

    from scout_index import default_index_dir
    index_dir = args.index_dir or default_index_dir()
    premises = load_premises(index_dir)
    if not premises:
        print(f"no types.jsonl under {index_dir}", file=sys.stderr)
        return 1

    # The index now spans Mathlib, so the corpus size is a real API spend and a
    # real resident set. Say what it is BEFORE paying for it.
    comp = corpus_composition(premises)
    print(f"corpus: {len(premises)} premises "
          + ", ".join(f"{ns}={n}" for ns, n in comp.items()), file=sys.stderr)
    if args.max_premises is not None and len(premises) > args.max_premises:
        import domain_pack
        own_namespaces = domain_pack.load(
            args.domain or domain_pack.DEFAULT_NAME).own_namespaces

        def _ours(p) -> bool:
            mod = p.get("module") or ""
            return any(mod == ns or mod.startswith(ns + ".") for ns in own_namespaces)

        own = [p for p in premises if _ours(p)]
        rest = [p for p in premises if not _ours(p)]
        premises = (own + rest)[:args.max_premises]
        print(f"capped to {len(premises)} (--max-premises); "
              f"{len(own)} own records kept first", file=sys.stderr)
    if args.dry_run:
        print(f"dry run — {len(premises)} premises would be embedded in "
              f"{(len(premises) + args.batch - 1) // args.batch} batches", file=sys.stderr)
        return 0

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("MISTRAL_API_KEY not set", file=sys.stderr)
        return 2

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
    mb = os.path.getsize(vectors_path(out_path)) / 1e6
    print(f"wrote {out_path} + {os.path.basename(vectors_path(out_path))} "
          f"({len(premises)} premises, {mb:.1f} MB, model={args.model})")
    return 0


if __name__ == "__main__":
    sys.exit(build_cli())
