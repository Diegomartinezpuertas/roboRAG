"""Tests for deciding when two observations of a place are one memory (ADR-025).

The bug these guard against was measured on a real run: 28 scene memories,
12 distinct descriptions, one of them stored eleven times — because a room is
many 0.5 m cells and each cell the robot reached became another memory.
"""

import math

import pytest

from robot_rag.scene_merge import (
    SEED_ID_PREFIX,
    find_merge_target,
    legacy_group_key,
    merged_observations,
    plan_compaction,
    scene_group_key,
)


def scene(entry_id, x, y, key='colors=brown+gray;clutter=open', map_id='live', zone='', **extra):
    """A stored scene memory as ChromaManager.list_documents returns it."""
    metadata = {'pose_x': x, 'pose_y': y, 'map_id': map_id, 'room_zone': zone,
                'group_key': key, **extra}
    return {'id': entry_id, 'document': f'area at (x={x}, y={y})', 'metadata': metadata}


# --- the group key ---------------------------------------------------------

def test_color_order_does_not_split_one_look_in_two():
    """"gray and brown" vs "brown and gray" flips on a few pixels; same place."""
    assert scene_group_key(['gray', 'brown'], 'open') == scene_group_key(['brown', 'gray'], 'open')


def test_the_clutter_class_does_split_looks():
    assert scene_group_key(['gray'], 'open') != scene_group_key(['gray'], 'cluttered')


def test_the_key_is_normalized_and_readable():
    assert scene_group_key([' Gray', 'brown', 'gray'], 'OPEN') == 'colors=brown+gray;clutter=open'
    assert scene_group_key([], '') == 'colors=none;clutter=unknown'


# --- choosing the memory to merge into -------------------------------------

def test_the_nearest_candidate_within_the_radius_is_chosen():
    candidates = [scene('far', 1.5, 0.0), scene('near', 0.5, 0.0)]
    assert find_merge_target(candidates, 0.0, 0.0, radius=2.0)['id'] == 'near'


def test_nothing_is_merged_beyond_the_radius():
    assert find_merge_target([scene('far', 3.0, 0.0)], 0.0, 0.0, radius=2.0) is None


def test_a_zero_radius_disables_merging():
    assert find_merge_target([scene('same', 0.0, 0.0)], 0.0, 0.0, radius=0.0) is None


def test_seeded_benchmark_fixtures_are_never_merge_targets():
    """Fixtures sit at fixed coordinates on purpose (eval/seed_memory.py)."""
    fixture = scene(f'{SEED_ID_PREFIX}estacion_a', 0.0, 0.0)
    assert find_merge_target([fixture], 0.0, 0.0, radius=2.0) is None


def test_candidates_without_a_pose_are_ignored():
    broken = {'id': 'scene-x', 'metadata': {'pose_x': None}}
    assert find_merge_target([broken], 0.0, 0.0, radius=2.0) is None


@pytest.mark.parametrize(('metadata', 'expected'), [
    ({}, 2), ({'observations': 1}, 2), ({'observations': 7}, 8), ({'observations': 'x'}, 2),
])
def test_observation_counts_start_at_one_for_memories_that_predate_counting(metadata, expected):
    assert merged_observations(metadata) == expected


# --- reading keys back out of old memories ---------------------------------

@pytest.mark.parametrize(('text', 'key'), [
    ('area at (x=-0.74, y=-1.08) in unknown area: predominantly gray and brown, '
     'an open, uncluttered space', 'colors=brown+gray;clutter=open'),
    ('area at (x=1.81, y=-0.18) in unknown area: predominantly brown and gray, '
     'a moderately furnished space (3 obstacle groups nearby)',
     'colors=brown+gray;clutter=moderate'),
    ('area at (x=2.72, y=0.08) in unknown area: predominantly gray and brown, a cluttered '
     'space with many objects (6 obstacle groups nearby)', 'colors=brown+gray;clutter=cluttered'),
    ('kitchen at (x=-1.48, y=-0.50) in cocina: predominantly brown and gray, an open, '
     'uncluttered space Observed inside the kitchen ("cocina"), where meals are cooked',
     'colors=brown+gray;clutter=open'),
])
def test_legacy_scene_descriptions_yield_the_key_they_would_have_had(text, key):
    assert legacy_group_key(text) == key


@pytest.mark.parametrize('text', [
    'estacion_a at (x=-2.19, y=-1.61) in unknown area: estacion_a, a named location',
    'kitchen at (x=1.75, y=-0.75) in cocina: User-defined zone "cocina" covering x[1.00, 2.50]',
])
def test_text_that_is_not_a_scene_description_has_no_key(text):
    assert legacy_group_key(text) is None


# --- compacting what is already stored -------------------------------------

def test_one_look_in_one_area_collapses_to_one_memory():
    entries = [scene('scene-a', 0.0, 0.0), scene('scene-b', 0.5, 0.0), scene('scene-c', 0.0, 0.8)]
    (group,) = plan_compaction(entries, radius=2.0)
    assert sorted([group.keep_id, *group.drop_ids]) == ['scene-a', 'scene-b', 'scene-c']
    assert group.observations == 3
    assert group.group_key == 'colors=brown+gray;clutter=open'


def test_the_kept_memory_is_the_member_nearest_the_centre():
    """A pose the robot really reached, and the most central of them."""
    entries = [scene('scene-a', 0.0, 0.0), scene('scene-b', 1.0, 0.0), scene('scene-c', 2.0, 0.0)]
    (group,) = plan_compaction(entries, radius=2.0)
    assert group.keep_id == 'scene-b'


@pytest.mark.parametrize('other', [
    scene('scene-b', 0.3, 0.0, zone='cocina'),          # another room, however alike
    scene('scene-b', 0.3, 0.0, map_id='old-map'),       # another map's coordinates
    scene('scene-b', 0.3, 0.0, key='colors=white;clutter=open'),  # another look
])
def test_memories_never_merge_across_zones_map_sessions_or_looks(other):
    assert plan_compaction([scene('scene-a', 0.0, 0.0), other], radius=2.0) == []


def test_fixtures_and_non_scene_memories_are_left_alone():
    entries = [
        scene('scene-a', 0.0, 0.0),
        scene(f'{SEED_ID_PREFIX}estacion_a', 0.1, 0.0),
        {'id': 'landmark-estacion_a', 'document': 'estacion_a at …',
         'metadata': {'pose_x': 0.1, 'pose_y': 0.0, 'map_id': 'live'}},
        {'id': 'zone-cocina', 'document': 'kitchen at …',
         'metadata': {'pose_x': 0.2, 'pose_y': 0.0, 'map_id': 'live'}},
    ]
    assert plan_compaction(entries, radius=2.0) == []


def test_a_legacy_memory_without_a_key_is_grouped_by_its_text():
    old = {'id': 'scene-old', 'document': 'area at (x=0.1, y=0.0) in unknown area: '
           'predominantly gray and brown, an open, uncluttered space',
           'metadata': {'pose_x': 0.1, 'pose_y': 0.0, 'map_id': 'live', 'room_zone': ''}}
    (group,) = plan_compaction([scene('scene-new', 0.0, 0.0), old], radius=2.0)
    assert {group.keep_id, *group.drop_ids} == {'scene-new', 'scene-old'}


def _survivors(entries, groups):
    dropped = {i for g in groups for i in g.drop_ids}
    return [e for e in entries if e['id'] not in dropped]


def test_compaction_is_idempotent_even_for_a_chain_of_observations():
    """Regression: clustering seeded by id order used to leave two survivors
    within the radius, so a second run folded more memories. The plan must reach
    the state merge-on-write keeps live, and then change nothing."""
    entries = [scene(f'scene-{i}', 0.9 * i, 0.3 * (i % 2)) for i in range(8)]
    first = plan_compaction(entries, radius=2.0)
    assert plan_compaction(_survivors(entries, first), radius=2.0) == []


def test_no_two_survivors_of_one_look_lie_within_the_radius():
    entries = [scene(f'scene-{i}', 0.7 * (i % 5), 0.6 * (i // 5)) for i in range(15)]
    survivors = _survivors(entries, plan_compaction(entries, radius=1.5))
    for i, a in enumerate(survivors):
        for b in survivors[i + 1:]:
            distance = math.hypot(a['metadata']['pose_x'] - b['metadata']['pose_x'],
                                  a['metadata']['pose_y'] - b['metadata']['pose_y'])
            assert distance > 1.5


def test_observation_counts_accumulate_across_merged_memories():
    entries = [scene('scene-a', 0.0, 0.0, observations=4), scene('scene-b', 0.5, 0.0)]
    (group,) = plan_compaction(entries, radius=2.0)
    assert group.observations == 5
