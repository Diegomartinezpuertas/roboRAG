"""Tests for finding the running localizer (ADR-004 live map, ADR-035 saved map)."""

from robot_skills.localization import localizer_node


def test_a_live_map_is_localized_by_slam_toolbox():
    names = ['/bt_navigator', '/slam_toolbox', '/controller_server']
    assert localizer_node(names) == 'slam_toolbox'


def test_a_saved_map_loaded_read_only_is_localized_by_amcl():
    assert localizer_node(['bt_navigator', 'map_server', 'amcl']) == 'amcl'


def test_nothing_is_picked_before_a_localizer_appears():
    assert localizer_node(['bt_navigator', 'lifecycle_manager_navigation']) is None
    assert localizer_node([]) is None


def test_names_are_matched_whole():
    assert localizer_node(['amcl_pose_relay', 'my_slam_toolbox_wrapper']) is None
