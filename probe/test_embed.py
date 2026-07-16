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
