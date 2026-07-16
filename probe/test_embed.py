"""Pure tests for embedding retrieval — injected embed_fn, no network/daemon."""
from __future__ import annotations

import math

import embed


def test_parse_embeddings_orders_by_index():
    # /v1/embeddings may return items out of order; we must realign by "index".
    data = {"data": [
        {"index": 1, "embedding": [0.0, 1.0]},
        {"index": 0, "embedding": [1.0, 0.0]},
    ]}
    assert embed._parse_embeddings(data) == [[1.0, 0.0], [0.0, 1.0]]


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


def test_embedding_index_save_load_roundtrip(tmp_path):
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]
    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(lambda texts: [[1.0, 0.0] for _ in texts])
    path = str(tmp_path / "cache.json")
    idx.save(path)
    loaded = embed.EmbeddingIndex.load(path, premises, "fake")
    assert loaded is not None
    assert loaded.vectors == idx.vectors


def test_embedding_index_load_rejects_model_mismatch(tmp_path):
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]
    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(lambda texts: [[1.0, 0.0] for _ in texts])
    path = str(tmp_path / "cache.json")
    idx.save(path)
    assert embed.EmbeddingIndex.load(path, premises, "other-model") is None


def test_embedding_index_load_rejects_corpus_mismatch(tmp_path):
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]
    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(lambda texts: [[1.0, 0.0] for _ in texts])
    path = str(tmp_path / "cache.json")
    idx.save(path)
    other = [{"name": "MathFin.forwardRate", "type": "ℝ → ℝ", "docString": ""}]
    assert embed.EmbeddingIndex.load(path, other, "fake") is None


def test_embedding_index_load_fails_soft_on_missing_vectors(tmp_path):
    import json as _json
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]
    idx = embed.EmbeddingIndex(premises, model="fake")
    path = str(tmp_path / "cache.json")
    with open(path, "w") as f:
        _json.dump({"model": "fake", "corpus_hash": idx.hash}, f)
    assert embed.EmbeddingIndex.load(path, premises, "fake") is None


def test_cache_path_is_per_model_under_index_dir():
    assert embed.cache_path("index", "mistral-embed") == "index/embeddings-mistral-embed.json"
