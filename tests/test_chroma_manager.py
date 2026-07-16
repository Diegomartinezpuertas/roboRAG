"""Tests for the ChromaDB CRUD/query wrapper (cosine distance -> score)."""

import pytest

from robot_rag.chroma_manager import ChromaManager


def test_add_count_query(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['memory'])
    assert mgr.count('memory') == 0
    mgr.add(
        'memory',
        documents=['hello world', 'goodbye'],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{'a': 1}, {'a': 2}],
        ids=['1', '2'],
    )
    assert mgr.count('memory') == 2

    docs, scores = mgr.query('memory', [1.0, 0.0], top_k=2)
    assert docs[0] == 'hello world'         # closest to the query vector
    assert scores[0] >= scores[1]           # ordered by relevance
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[0] == pytest.approx(1.0, abs=1e-3)  # identical vector -> ~1.0


def test_upsert_is_idempotent_by_id(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['memory'])
    for _ in range(2):
        mgr.add('memory', ['doc'], [[1.0, 0.0]], [{'a': 1}], ids=['same'])
    assert mgr.count('memory') == 1


def test_query_empty_collection_returns_empty(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['memory'])
    docs, scores = mgr.query('memory', [1.0, 0.0], top_k=3)
    assert docs == [] and scores == []


def test_unknown_collection_raises(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['memory'])
    with pytest.raises(ValueError):
        mgr.query('does_not_exist', [1.0, 0.0], 1)
