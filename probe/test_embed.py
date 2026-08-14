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


def test_make_embedding_retrieve_fn_is_str_to_str():
    premises = [{"name": "MathFin.zcb", "type": "ℝ → ℝ", "docString": ""}]

    def fake_embed(texts):
        return [[1.0, 0.0] for _ in texts]

    idx = embed.EmbeddingIndex(premises, model="fake")
    idx.build(fake_embed)
    fn = embed.make_embedding_retrieve_fn(idx, k=1, embed_fn=fake_embed)
    out = fn("MathFin.zcb")
    assert isinstance(out, str) and "MathFin.zcb" in out


def test_is_usable_premise_drops_internal_names():
    for n in ["_private.MathFin.X.0.MathFin.Y._simp_1_7", "MathFin.Foo._proof_3",
              "MathFin.OnePeriodVector.IsEMM.casesOn", "MathFin.Bar.recOn",
              "MathFin.Baz.injEq", "MathFin.Qux.eq_1", "MathFin.Thing.congr_simp",
              "MathFin.Struct.mk", ""]:
        assert embed._is_usable_premise(n) is False, n


def test_is_usable_premise_keeps_real_decls():
    for n in ["MathFin.bsCall_asset_piece_integral", "MathFin.zcb",
              "MathFin.OnePeriodVector.IsEMM.exists_of_physical", "MathFin.gbmValue"]:
        assert embed._is_usable_premise(n) is True, n


def test_load_premises_filters_internal(tmp_path):
    import json as _json
    (tmp_path / "types.jsonl").write_text("\n".join(_json.dumps(r) for r in [
        {"name": "MathFin.zcb", "type": "ℝ → ℝ"},
        {"name": "_private.MathFin.X.0.Y._simp_1", "type": "z"},
        {"name": "MathFin.Foo.casesOn", "type": "w"},
    ]))
    assert [r["name"] for r in embed.load_premises(str(tmp_path))] == ["MathFin.zcb"]


def test_load_premises_drops_allowcompletion_false(tmp_path):
    # Lean's own flag drops auto-gen internals even when the NAME looks clean.
    import json as _json
    (tmp_path / "types.jsonl").write_text("\n".join(_json.dumps(r) for r in [
        {"name": "MathFin.realLemma", "type": "P", "allowCompletion": True},
        {"name": "MathFin.autoGenThing", "type": "Q", "allowCompletion": False},
    ]))
    assert [r["name"] for r in embed.load_premises(str(tmp_path))] == ["MathFin.realLemma"]


# --- flat float32 store + binary sidecar (2026-08-14) -------------------------
# The corpus grew from MathFin-only (~2.8k) to MathFin + its reached Mathlib
# neighbourhoods, so the vectors moved out of the JSON. These pin the properties
# that move made load-bearing.

import array
import json as _json
import os as _os
import sys as _sys
import tempfile


def _prem(n):
    return [{"name": f"L{i}", "module": "MathFin.Foo", "type": "T"} for i in range(n)]


def _rows(n, dim=4):
    # distinct, non-degenerate rows so ranking is well-defined
    return [[float((i + j) % 7) + 1.0 for j in range(dim)] for i in range(n)]


def test_save_writes_a_binary_sidecar_not_inline_vectors():
    with tempfile.TemporaryDirectory() as d:
        idx = embed.EmbeddingIndex(_prem(3), model="fake")
        idx.vectors = _rows(3)
        path = embed.cache_path(d, "fake")
        idx.save(path)
        blob = _json.load(open(path))
        assert "vectors" not in blob                     # the 1 GB-at-scale mistake
        assert blob["dim"] == 4 and blob["count"] == 3
        assert _os.path.isfile(embed.vectors_path(path))
        # 3 rows x 4 dims x 4 bytes
        assert _os.path.getsize(embed.vectors_path(path)) == 48


def test_sidecar_roundtrip_preserves_ranking():
    with tempfile.TemporaryDirectory() as d:
        prem = _prem(5)
        idx = embed.EmbeddingIndex(prem, model="fake")
        idx.vectors = _rows(5)
        path = embed.cache_path(d, "fake")
        idx.save(path)
        loaded = embed.EmbeddingIndex.load(path, prem, "fake")
        assert loaded is not None
        q = [1.0, 2.0, 3.0, 4.0]
        assert (embed.top_k_flat(q, loaded._flat, loaded._dim, 5)
                == embed.top_k_flat(q, idx._flat, idx._dim, 5))


def test_top_k_flat_agrees_with_top_k():
    rows = _rows(6)
    flat = array.array("f", (x for r in rows for x in r))
    q = [0.5, 1.0, 0.0, 2.0]
    # float32 vs float64 must not reorder these well-separated rows
    assert embed.top_k_flat(q, flat, 4, 3) == embed.top_k(q, rows, 3)


def test_precomputed_norms_do_not_change_the_ranking():
    rows = _rows(6)
    idx = embed.EmbeddingIndex(_prem(6), model="fake")
    idx.vectors = rows
    q = [0.5, 1.0, 0.0, 2.0]
    assert (embed.top_k_flat(q, idx._flat, idx._dim, 4, norms=idx._row_norms())
            == embed.top_k_flat(q, idx._flat, idx._dim, 4))


def test_legacy_inline_cache_still_loads():
    # a cache written before the sidecar existed must not be silently discarded:
    # rebuilding costs a full re-embed of the corpus.
    with tempfile.TemporaryDirectory() as d:
        prem = _prem(2)
        idx = embed.EmbeddingIndex(prem, model="fake")
        path = embed.cache_path(d, "fake")
        with open(path, "w") as f:
            _json.dump({"model": "fake", "corpus_hash": idx.hash,
                        "vectors": [[1.0, 0.0], [0.0, 1.0]]}, f)
        loaded = embed.EmbeddingIndex.load(path, prem, "fake")
        assert loaded is not None and loaded.vectors == [[1.0, 0.0], [0.0, 1.0]]


def test_missing_sidecar_is_stale_not_a_crash():
    with tempfile.TemporaryDirectory() as d:
        prem = _prem(3)
        idx = embed.EmbeddingIndex(prem, model="fake")
        idx.vectors = _rows(3)
        path = embed.cache_path(d, "fake")
        idx.save(path)
        _os.remove(embed.vectors_path(path))
        assert embed.EmbeddingIndex.load(path, prem, "fake") is None


def test_truncated_sidecar_is_stale_not_a_crash():
    with tempfile.TemporaryDirectory() as d:
        prem = _prem(3)
        idx = embed.EmbeddingIndex(prem, model="fake")
        idx.vectors = _rows(3)
        path = embed.cache_path(d, "fake")
        idx.save(path)
        with open(embed.vectors_path(path), "r+b") as f:
            f.truncate(20)
        assert embed.EmbeddingIndex.load(path, prem, "fake") is None


def test_foreign_byteorder_cache_is_rejected():
    # float32 is written native; decoding it the other way round yields noise,
    # and noise that ranks confidently is worse than no cache at all.
    with tempfile.TemporaryDirectory() as d:
        prem = _prem(3)
        idx = embed.EmbeddingIndex(prem, model="fake")
        idx.vectors = _rows(3)
        path = embed.cache_path(d, "fake")
        idx.save(path)
        blob = _json.load(open(path))
        blob["byteorder"] = "big" if _sys.byteorder == "little" else "little"
        _json.dump(blob, open(path, "w"))
        assert embed.EmbeddingIndex.load(path, prem, "fake") is None


def test_save_before_build_is_an_error_not_an_empty_cache():
    with tempfile.TemporaryDirectory() as d:
        idx = embed.EmbeddingIndex(_prem(2), model="fake")
        try:
            idx.save(embed.cache_path(d, "fake"))
        except ValueError:
            return
        assert False, "saving an unbuilt index must raise, not write a bad cache"


def test_vectors_path_sits_beside_the_json():
    assert embed.vectors_path("index/embeddings-mistral-embed.json") == \
        "index/embeddings-mistral-embed.f32"


def test_corpus_composition_counts_by_namespace():
    prem = [{"module": "MathFin.Foo"}, {"module": "MathFin.Bar"},
            {"module": "Mathlib.MeasureTheory.X"}, {"module": None}]
    assert embed.corpus_composition(prem) == {"MathFin": 2, "Mathlib": 1, "?": 1}


def test_dry_run_reports_without_touching_the_api(capsys, monkeypatch):
    # the guard against silently spending an embed budget on a corpus nobody sized
    monkeypatch.setattr(embed, "mistral_embed",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("called the API")))
    with tempfile.TemporaryDirectory() as d:
        with open(_os.path.join(d, "types.jsonl"), "w") as f:
            for i in range(5):
                f.write(_json.dumps({"name": f"MathFin.l{i}", "module": "MathFin.Foo",
                                     "type": "T", "allowCompletion": True}) + "\n")
        assert embed.build_cli(["--index-dir", d, "--dry-run"]) == 0
        err = capsys.readouterr().err
        assert "corpus: 5 premises" in err and "MathFin=5" in err


def test_max_premises_keeps_own_records_first(capsys, monkeypatch):
    monkeypatch.setattr(embed, "mistral_embed", lambda *a, **k: [])
    with tempfile.TemporaryDirectory() as d:
        with open(_os.path.join(d, "types.jsonl"), "w") as f:
            for i in range(3):
                f.write(_json.dumps({"name": f"Mathlib.m{i}", "module": "Mathlib.X",
                                     "type": "T", "allowCompletion": True}) + "\n")
            f.write(_json.dumps({"name": "MathFin.a", "module": "MathFin.Foo",
                                 "type": "T", "allowCompletion": True}) + "\n")
        assert embed.build_cli(["--index-dir", d, "--dry-run", "--max-premises", "2"]) == 0
        err = capsys.readouterr().err
        assert "capped to 2" in err and "1 own records kept first" in err
