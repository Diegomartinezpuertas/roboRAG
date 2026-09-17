"""Tests for map-session identity and the coordinate-memory filter (ADR-019).

These pin down the fix for the stale-coordinate bug: a memory tagged with one
map session must not be returned when a different session is active, so a pose
from a dead SLAM map never resurfaces as if current.
"""

from robot_rag.chroma_manager import ChromaManager
from robot_rag.map_session import MapSession, read_active_map_id


# --- MapSession ------------------------------------------------------------

def test_current_id_is_created_once_and_stays_stable(tmp_path):
    session = MapSession(tmp_path)
    first = session.current_id()
    assert first
    assert session.current_id() == first, 'the id must persist across reads'


def test_current_id_survives_a_new_instance(tmp_path):
    first = MapSession(tmp_path).current_id()
    assert MapSession(tmp_path).current_id() == first, 'must persist on disk'


def test_rotate_starts_a_fresh_session(tmp_path):
    session = MapSession(tmp_path)
    old = session.current_id()
    new = session.rotate()
    assert new != old
    assert session.current_id() == new


def test_set_id_pins_a_specific_session(tmp_path):
    """Used when loading a saved map so its memories match again."""
    session = MapSession(tmp_path)
    session.current_id()
    session.set_id('saved-map-42')
    assert session.current_id() == 'saved-map-42'


def test_read_active_map_id_does_not_create_a_session(tmp_path):
    """A read-only producer (report_skill) must not mint a session of its own."""
    assert read_active_map_id(tmp_path) == ''
    assert not (tmp_path / 'session.json').exists()


def test_read_active_map_id_returns_the_set_id(tmp_path):
    MapSession(tmp_path).set_id('abc123')
    assert read_active_map_id(tmp_path) == 'abc123'


# --- the coordinate filter (ChromaManager.query where=) --------------------

def _seed(mgr, doc_id, text, map_id):
    mgr.add(
        'semantic_map',
        documents=[text],
        embeddings=[[1.0, 0.0]],
        metadatas=[{'map_id': map_id}],
        ids=[doc_id],
    )


def test_filter_returns_only_the_active_session(tmp_path):
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['semantic_map'])
    _seed(mgr, 'cur', 'base at (1.0, 2.0)', 'current')
    _seed(mgr, 'old', 'base at (-9.0, -9.0)', 'dead_map')

    docs, _ = mgr.query('semantic_map', [1.0, 0.0], top_k=5, where={'map_id': 'current'})
    assert docs == ['base at (1.0, 2.0)'], 'the dead-map coordinate must be filtered out'


def test_untagged_memory_is_excluded_by_a_session_filter(tmp_path):
    """Pre-versioning memories (map_id='') must not leak into a tagged session."""
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['semantic_map'])
    _seed(mgr, 'legacy', 'base at (-9.0, -9.0)', '')

    docs, _ = mgr.query('semantic_map', [1.0, 0.0], top_k=5, where={'map_id': 'current'})
    assert docs == []


def test_no_filter_returns_everything(tmp_path):
    """knowledge_base is queried without a filter and must be unaffected."""
    mgr = ChromaManager(str(tmp_path / 'chroma'), ['semantic_map'])
    _seed(mgr, 'a', 'one', 'm1')
    _seed(mgr, 'b', 'two', 'm2')
    docs, _ = mgr.query('semantic_map', [1.0, 0.0], top_k=5)
    assert len(docs) == 2


# --- telling whether a save actually wrote the map (ADR-035) ----------------

from robot_rag.map_session import map_image_stamp, saved_map_stamp  # noqa: E402


def test_a_map_without_both_files_has_no_stamp(tmp_path):
    base = tmp_path / 'house' / 'map'
    assert saved_map_stamp(base) is None
    base.parent.mkdir()
    base.with_suffix('.posegraph').write_bytes(b'graph')     # no map.data
    assert saved_map_stamp(base) is None


def test_the_stamp_changes_when_the_map_is_rewritten_and_not_otherwise(tmp_path):
    """A read-only SLAM answers the save without writing: the stamp must not move."""
    import os
    base = tmp_path / 'house' / 'map'
    base.parent.mkdir()
    base.with_suffix('.posegraph').write_bytes(b'graph')
    base.with_suffix('.data').write_bytes(b'data')
    before = saved_map_stamp(base)
    assert before is not None
    assert saved_map_stamp(base) == before
    os.utime(base.with_suffix('.posegraph'), ns=(before + 10**9, before + 10**9))
    assert saved_map_stamp(base) != before


def test_the_image_stamp_needs_the_yaml_and_the_image(tmp_path):
    import os
    base = tmp_path / 'house' / 'map'
    base.parent.mkdir()
    assert map_image_stamp(base) is None
    base.with_suffix('.yaml').write_text('image: map.pgm')
    assert map_image_stamp(base) is None
    base.with_suffix('.pgm').write_bytes(b'P5')
    before = map_image_stamp(base)
    assert before is not None
    os.utime(base.with_suffix('.pgm'), ns=(before + 10**9, before + 10**9))
    assert map_image_stamp(base) != before
