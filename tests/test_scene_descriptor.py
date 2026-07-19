"""Tests for the classical scene descriptor (colors + LIDAR clutter)."""

import numpy as np

from robot_skills.scene_descriptor import (
    clutter_metrics,
    describe_scene,
    dominant_colors,
)


def solid_image(rgb, h=64, w=64):
    return np.full((h, w, 3), rgb, dtype=np.uint8)


def test_dominant_colors_white():
    assert dominant_colors(solid_image((245, 245, 245))) == ['white']


def test_dominant_colors_brown():
    # Dark orange hue -> brown (wood/furniture).
    assert dominant_colors(solid_image((120, 70, 20))) == ['brown']


def test_dominant_colors_two_colors():
    img = solid_image((245, 245, 245))
    img[:, :32] = (20, 60, 200)  # half blue
    colors = dominant_colors(img)
    assert set(colors) == {'white', 'blue'}


def test_dominant_colors_minor_color_dropped():
    img = solid_image((245, 245, 245))
    img[:4, :4] = (200, 30, 30)  # <1% red
    assert dominant_colors(img) == ['white']


def test_clutter_open_space():
    metrics = clutter_metrics([3.5] * 360)
    assert metrics['clutter'] == 'open'
    assert metrics['obstacle_clusters'] == 0


def test_clutter_many_clusters():
    # 8 separate near-objects around the scan.
    ranges = [3.5] * 360
    for start in range(0, 360, 45):
        for i in range(start, start + 5):
            ranges[i] = 1.0
    metrics = clutter_metrics(ranges)
    assert metrics['obstacle_clusters'] >= 6
    assert metrics['clutter'] == 'cluttered'


def test_clutter_handles_inf_nan():
    ranges = [float('inf'), float('nan'), 2.0, 2.0, 3.5] * 20
    metrics = clutter_metrics(ranges)
    assert 0.0 <= metrics['near_fraction'] <= 1.0
    assert np.isfinite(metrics['median_clearance_m'])


def test_describe_scene_combines_sources():
    img = solid_image((245, 245, 245))
    result = describe_scene(img, [3.5] * 360)
    assert 'white' in result['description']
    assert 'open' in result['description']
    assert result['colors'] == ['white']


def test_describe_scene_scan_only():
    result = describe_scene(None, [1.0] * 360)
    assert result['colors'] == []
    assert 'space' in result['description']


def test_describe_scene_no_sensors():
    result = describe_scene(None, None)
    assert result['description'] == 'no sensor data available'
