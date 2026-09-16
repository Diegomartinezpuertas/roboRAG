"""Merge-on-write against a real ChromaDB (ADR-025).

SemanticMap only needs an embedder and an object shaped like SemanticObject,
so the whole storage path runs here without ROS or Ollama.
"""

from types import SimpleNamespace

import pytest

from robot_rag.chroma_manager import ChromaManager
from robot_rag.scene_merge import scene_group_key
from robot_rag.semantic_map import COLLECTION_NAME, SemanticMap

KEY = scene_group_key(['gray', 'brown'], 'open')


class FakeEmbedder:
    def embed_text(self, text):
        return [1.0, 0.0]


def observation(object_id, x, y, key=KEY, zone='', description='predominantly gray and brown'):
    """A SemanticObject look-alike, as the skills node sends it."""
    return SimpleNamespace(
        object_id=object_id, label='area', confidence=1.0, description=description,
        room_zone=zone, group_key=key,
        pose=SimpleNamespace(position=SimpleNamespace(x=x, y=y)),
    )


@pytest.fixture
def store(tmp_path):
    chroma = ChromaManager(str(tmp_path / 'chroma'), [COLLECTION_NAME])
    return chroma, SemanticMap(chroma, FakeEmbedder(), merge_radius=2.0)


def entries(chroma):
    return {e['id']: e for e in chroma.list_documents(COLLECTION_NAME, 100)}


def test_a_new_place_is_stored_under_its_proposed_id(store):
    chroma, semantic_map = store
    assert semantic_map.upsert_object(observation('scene-0.0-0.0', 0.0, 0.0), 'live') == (
        'scene-0.0-0.0', False,
    )
    stored = entries(chroma)['scene-0.0-0.0']['metadata']
    assert stored['observations'] == 1 and stored['group_key'] == KEY


def test_re_observing_the_same_look_nearby_updates_the_existing_memory(store):
    chroma, semantic_map = store
    semantic_map.upsert_object(observation('scene-0.0-0.0', 0.0, 0.0), 'live')
    object_id, merged = semantic_map.upsert_object(
        observation('scene-1.0-0.5', 1.1, 0.4, description='predominantly brown and gray'), 'live',
    )
    assert (object_id, merged) == ('scene-0.0-0.0', True)
    stored = entries(chroma)
    assert list(stored) == ['scene-0.0-0.0']                 # still one memory
    memory = stored['scene-0.0-0.0']
    assert memory['metadata']['observations'] == 2
    # The anchor does not move, and the coordinates the planner reads agree with it.
    assert (memory['metadata']['pose_x'], memory['metadata']['pose_y']) == (0.0, 0.0)
    assert 'at (x=0.00, y=0.00)' in memory['document']
    assert 'brown and gray' in memory['document']            # description refreshed


def test_a_chain_of_observations_does_not_drag_the_memory_along(store):
    """Anchored: walking a long corridor of one colour leaves several memories."""
    chroma, semantic_map = store
    for step in range(6):
        semantic_map.upsert_object(observation(f'scene-{step}', step * 1.0, 0.0), 'live')
    assert len(entries(chroma)) >= 2


@pytest.mark.parametrize('kwargs', [
    {'x': 3.0, 'y': 0.0},                               # beyond the radius
    {'x': 0.5, 'y': 0.0, 'zone': 'cocina'},             # another room
    {'x': 0.5, 'y': 0.0, 'key': scene_group_key(['white'], 'open')},  # another look
])
def test_observations_that_are_not_the_same_place_stay_apart(store, kwargs):
    chroma, semantic_map = store
    semantic_map.upsert_object(observation('scene-a', 0.0, 0.0), 'live')
    params = {'x': 0.0, 'y': 0.0, **kwargs}
    _, merged = semantic_map.upsert_object(observation('scene-b', **params), 'live')
    assert merged is False
    assert set(entries(chroma)) == {'scene-a', 'scene-b'}


def test_memories_from_another_map_session_are_never_merged_into(store):
    chroma, semantic_map = store
    semantic_map.upsert_object(observation('scene-a', 0.0, 0.0), 'old-map')
    _, merged = semantic_map.upsert_object(observation('scene-b', 0.1, 0.0), 'live')
    assert merged is False


def test_objects_without_a_group_key_keep_plain_upsert_semantics(store):
    """Zones, landmarks and seeded fixtures carry no key: stored by id, as before."""
    chroma, semantic_map = store
    semantic_map.upsert_object(observation('zone-cocina', 0.0, 0.0, key=''), 'live')
    _, merged = semantic_map.upsert_object(observation('landmark-x', 0.1, 0.0, key=''), 'live')
    assert merged is False
    assert set(entries(chroma)) == {'zone-cocina', 'landmark-x'}
