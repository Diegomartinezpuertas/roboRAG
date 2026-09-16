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


# --- inspection (what the dashboard's memory viewer reads) -----------------

def _populated(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['memory', 'empty'])
    mgr.add(
        'memory',
        documents=['kitchen at (x=1.0, y=2.0)', 'corridor at (x=0.0, y=0.0)'],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        metadatas=[{'map_id': 'live', 'label': 'kitchen'}, {'map_id': 'dead', 'label': 'area'}],
        ids=['kitchen', 'corridor'],
    )
    return mgr


def test_list_documents_returns_entries_with_ids_and_metadata(tmp_path):
    """Browsing must work with no query — and therefore with no embedder."""
    entries = _populated(tmp_path).list_documents('memory', limit=10)
    assert {e['id'] for e in entries} == {'kitchen', 'corridor'}
    kitchen = next(e for e in entries if e['id'] == 'kitchen')
    assert kitchen['document'].startswith('kitchen at')
    assert kitchen['metadata']['label'] == 'kitchen'


def test_list_documents_honours_the_limit_and_the_metadata_filter(tmp_path):
    mgr = _populated(tmp_path)
    assert len(mgr.list_documents('memory', limit=1)) == 1
    scoped = mgr.list_documents('memory', limit=10, where={'map_id': 'live'})
    assert [e['id'] for e in scoped] == ['kitchen']


def test_list_documents_of_an_empty_collection_is_empty(tmp_path):
    assert _populated(tmp_path).list_documents('empty', limit=10) == []


def test_query_documents_carries_the_score_alongside_id_and_metadata(tmp_path):
    hits = _populated(tmp_path).query_documents('memory', [1.0, 0.0], top_k=2)
    assert hits[0]['id'] == 'kitchen'
    assert hits[0]['score'] >= hits[1]['score']
    assert hits[0]['metadata']['map_id'] == 'live'


def test_query_and_query_documents_agree(tmp_path):
    """query() is the planner's thin view of the same retrieval."""
    mgr = _populated(tmp_path)
    docs, scores = mgr.query('memory', [1.0, 0.0], top_k=2)
    hits = mgr.query_documents('memory', [1.0, 0.0], top_k=2)
    assert docs == [h['document'] for h in hits]
    assert scores == [h['score'] for h in hits]


def test_stats_counts_every_collection_including_the_empty_ones(tmp_path):
    assert _populated(tmp_path).stats() == {'memory': 2, 'empty': 0}


def test_unknown_collection_raises_when_inspected(tmp_path):
    mgr = _populated(tmp_path)
    with pytest.raises(ValueError):
        mgr.list_documents('does_not_exist', limit=5)


def test_update_metadata_merges_keys_without_touching_the_text(tmp_path):
    """ChromaDB merges metadata on update: keys not given keep their value."""
    mgr = _populated(tmp_path)
    mgr.update_metadata('memory', ['kitchen'], [{'map_id': 'live', 'observations': 3}])
    kitchen = next(e for e in mgr.list_documents('memory', 10) if e['id'] == 'kitchen')
    assert kitchen['metadata'] == {'map_id': 'live', 'observations': 3, 'label': 'kitchen'}
    assert kitchen['document'].startswith('kitchen at')


def test_delete_removes_entries_and_ignores_unknown_ids(tmp_path):
    mgr = _populated(tmp_path)
    mgr.delete('memory', ['corridor', 'does-not-exist'])
    assert [e['id'] for e in mgr.list_documents('memory', 10)] == ['kitchen']


def test_existing_ids_reports_only_what_is_stored(tmp_path):
    mgr = _populated(tmp_path)
    found = mgr.existing_ids('memory', ['kitchen', 'ghost', 'corridor'])
    assert sorted(found) == ['corridor', 'kitchen']
    assert mgr.existing_ids('memory', []) == []
