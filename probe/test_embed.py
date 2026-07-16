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
