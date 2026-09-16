"""Tests for the one-off compaction of duplicates stored before ADR-025."""

import pytest

from robot_rag.chroma_manager import ChromaManager
from robot_rag.compact_memory import COLLECTIONS, compact


def seed_store(path):
    """A store shaped like the real one: duplicated scenes, fixtures, old maps."""
    chroma = ChromaManager(path, COLLECTIONS)
    rows = [
        ('scene-a', 'area: predominantly gray and brown, an open, uncluttered space',
         {'pose_x': 0.0, 'pose_y': 0.0, 'map_id': 'live', 'room_zone': ''}),
        ('scene-b', 'area: predominantly brown and gray, an open, uncluttered space',
         {'pose_x': 0.6, 'pose_y': 0.0, 'map_id': 'live', 'room_zone': ''}),
        ('scene-c', 'area: predominantly gray and brown, an open, uncluttered space',
         {'pose_x': 1.2, 'pose_y': 0.0, 'map_id': 'live', 'room_zone': ''}),
        ('scene-old', 'area: predominantly white, an open, uncluttered space',
         {'pose_x': 5.0, 'pose_y': 5.0, 'room_zone': ''}),                 # no session
        ('scene-other', 'area: predominantly white, an open, uncluttered space',
         {'pose_x': 5.0, 'pose_y': 5.0, 'map_id': 'saved-map', 'room_zone': ''}),
        ('zone-cocina', 'kitchen at …', {'pose_x': 0.1, 'pose_y': 0.0, 'map_id': 'live'}),
    ]
    chroma.add('semantic_map', [r[1] for r in rows], [[1.0, 0.0]] * len(rows),
               [r[2] for r in rows], ids=[r[0] for r in rows])
    return chroma


def ids(path):
    chroma = ChromaManager(path, COLLECTIONS)
    return {e['id'] for e in chroma.list_documents('semantic_map', 100)}


def not_running():
    return False


def test_a_dry_run_reports_and_writes_nothing(tmp_path):
    path = str(tmp_path / 'chroma')
    seed_store(path)
    report = compact(path, apply=False, is_rag_running=not_running)
    assert report['before'] == 6 and report['after'] == 4
    assert report['applied'] is False
    assert len(ids(path)) == 6


def test_applying_folds_duplicates_keeps_the_centre_and_backs_up_first(tmp_path):
    path = str(tmp_path / 'chroma')
    seed_store(path)
    report = compact(path, apply=True, is_rag_running=not_running)
    assert ids(path) == {'scene-b', 'scene-old', 'scene-other', 'zone-cocina'}
    kept = ChromaManager(path, COLLECTIONS).list_documents('semantic_map', 100)
    scene_b = next(e for e in kept if e['id'] == 'scene-b')['metadata']
    assert scene_b['observations'] == 3
    assert scene_b['group_key'] == 'colors=brown+gray;clutter=open'
    assert report['backup'] and len(ids(report['backup'])) == 6    # the backup is whole


def test_pruning_removes_only_memories_that_no_session_can_retrieve(tmp_path):
    """Another saved map's memories must survive: reloading it brings them back."""
    path = str(tmp_path / 'chroma')
    seed_store(path)
    compact(path, apply=True, prune_untagged=True, is_rag_running=not_running)
    assert 'scene-old' not in ids(path)
    assert {'scene-other', 'zone-cocina'} <= ids(path)


def test_it_refuses_to_write_while_rag_node_is_running(tmp_path):
    path = str(tmp_path / 'chroma')
    seed_store(path)
    with pytest.raises(RuntimeError):
        compact(path, apply=True, is_rag_running=lambda: True)
    assert len(ids(path)) == 6


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    path = str(tmp_path / 'chroma')
    seed_store(path)
    compact(path, apply=True, is_rag_running=not_running)
    second = compact(path, apply=True, is_rag_running=not_running)
    assert second['groups'] == [] and second['applied'] is False
