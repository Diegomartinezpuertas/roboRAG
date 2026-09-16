"""Tests for finding a saved SLAM map and loading it into SLAM Toolbox (ADR-026)."""

import pytest

from robot_bringup.saved_maps import (
    map_search_paths,
    resolve_saved_map,
    slam_params_for_saved_map,
)


def save_map(root, map_id):
    """Writes the two files a serialized SLAM Toolbox map consists of."""
    folder = root / map_id
    folder.mkdir(parents=True)
    (folder / 'map.posegraph').write_bytes(b'graph')
    (folder / 'map.data').write_bytes(b'data')
    return folder / 'map'


@pytest.fixture
def places(tmp_path):
    ws, share = tmp_path / 'ws', tmp_path / 'share'
    return ws, share


def test_a_map_saved_in_the_workspace_is_found(places):
    ws, share = places
    base = save_map(ws / 'data' / 'maps', 'casa')
    assert resolve_saved_map('casa', ws, share) == base


def test_a_map_shipped_with_the_repository_is_found(places):
    ws, share = places
    base = save_map(share / 'maps', 'house')
    assert resolve_saved_map('house', ws, share) == base


def test_the_workspace_copy_wins_over_the_shipped_one(places):
    """Re-saving a shipped id locally replaces it for you, repository untouched."""
    ws, share = places
    local = save_map(ws / 'data' / 'maps', 'house')
    save_map(share / 'maps', 'house')
    assert resolve_saved_map('house', ws, share) == local


def test_a_half_written_map_does_not_count(places):
    ws, share = places
    folder = ws / 'data' / 'maps' / 'casa'
    folder.mkdir(parents=True)
    (folder / 'map.posegraph').write_bytes(b'graph')          # no map.data
    with pytest.raises(FileNotFoundError):
        resolve_saved_map('casa', ws, share)


def test_an_unknown_map_fails_listing_where_it_looked(places):
    """A typo must fail the launch loudly, not start SLAM on an empty map."""
    ws, share = places
    with pytest.raises(FileNotFoundError) as error:
        resolve_saved_map('hosue', ws, share)
    for folder in map_search_paths('hosue', ws, share):
        assert str(folder.parent) in str(error.value)


@pytest.mark.parametrize('bad_id', ['', '.', '..', '../etc', 'a/b', 'casa nueva'])
def test_ids_that_could_escape_the_maps_directory_are_rejected(places, bad_id):
    ws, share = places
    with pytest.raises(ValueError):
        resolve_saved_map(bad_id, ws, share)


def test_slam_params_load_the_map_and_start_at_the_dock(tmp_path):
    params = {'slam_toolbox': {'ros__parameters': {
        'mode': 'mapping', 'resolution': 0.05, 'map_start_pose': [0.0, 0.0, 0.0],
    }}}
    rewritten = slam_params_for_saved_map(params, tmp_path / 'house' / 'map')
    ros = rewritten['slam_toolbox']['ros__parameters']
    assert ros['map_file_name'] == str(tmp_path / 'house' / 'map')
    assert ros['map_start_at_dock'] is True
    assert 'map_start_pose' not in ros            # mutually exclusive with the dock start
    assert ros['mode'] == 'mapping' and ros['resolution'] == 0.05


def test_the_original_params_are_not_modified(tmp_path):
    params = {'slam_toolbox': {'ros__parameters': {'mode': 'mapping'}}}
    slam_params_for_saved_map(params, tmp_path / 'map')
    assert params == {'slam_toolbox': {'ros__parameters': {'mode': 'mapping'}}}
