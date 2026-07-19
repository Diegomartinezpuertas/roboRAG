"""Classical scene descriptor: dominant colors + LIDAR clutter, no ML models.

Replaces the Qwen2.5-VL perception (ADR-014): vision-language models are
unreliable on this simulator's software-rendered frames, while pixel and
range statistics are exact. The output is a short English description with
the dominant colors and how cluttered the surroundings are — descriptive,
open-vocabulary text that the RAG memory can retrieve later ("go to the
white, open room", "where there were many objects").

Pure numpy; no ROS imports, so it is unit-testable in CI.
"""

from __future__ import annotations

import math

import numpy as np

# Fraction of pixels a color needs to be reported.
COLOR_MIN_SHARE = 0.18
MAX_COLORS = 2

# Ranges (m) used by the clutter heuristics.
NEAR_OBSTACLE_M = 2.5
GAP_JUMP_M = 0.6


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized RGB [0,1] -> (hue degrees, saturation, value)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    value = maxc
    delta = maxc - minc
    saturation = np.where(maxc > 0, delta / np.where(maxc == 0, 1, maxc), 0.0)

    hue = np.zeros_like(maxc)
    mask = delta > 0
    rc = np.where(mask, (maxc - r) / np.where(delta == 0, 1, delta), 0)
    gc = np.where(mask, (maxc - g) / np.where(delta == 0, 1, delta), 0)
    bc = np.where(mask, (maxc - b) / np.where(delta == 0, 1, delta), 0)
    hue = np.where(maxc == r, bc - gc, hue)
    hue = np.where(maxc == g, 2.0 + rc - bc, hue)
    hue = np.where(maxc == b, 4.0 + gc - rc, hue)
    hue = (hue / 6.0) % 1.0 * 360.0
    return hue, saturation, value


def _classify_pixel_colors(hue, sat, val) -> np.ndarray:
    """Maps HSV arrays to color-name indices (see COLOR_NAMES)."""
    names = np.full(hue.shape, 8, dtype=np.int8)  # default gray
    names[val < 0.18] = 9                                        # black
    white = (sat < 0.16) & (val > 0.8)
    names[white] = 7                                             # white
    chromatic = (sat >= 0.16) & (val >= 0.18)
    hue_bins = [
        (0, 20, 0),      # red
        (20, 50, 1),     # orange (brown when dark, handled below)
        (50, 70, 2),     # yellow
        (70, 165, 3),    # green
        (165, 260, 4),   # blue
        (260, 320, 5),   # purple
        (320, 360, 0),   # red again
    ]
    for lo, hi, idx in hue_bins:
        sel = chromatic & (hue >= lo) & (hue < hi)
        names[sel] = idx
    # Dark/medium orange reads as brown (wood, furniture).
    brown = chromatic & (hue >= 10) & (hue < 55) & (val < 0.72)
    names[brown] = 6
    return names


COLOR_NAMES = ('red', 'orange', 'yellow', 'green', 'blue', 'purple',
               'brown', 'white', 'gray', 'black')


def dominant_colors(rgb_image: np.ndarray, stride: int = 4) -> list[str]:
    """Returns up to MAX_COLORS dominant color names in an RGB uint8 image.

    Args:
        rgb_image: HxWx3 uint8 array.
        stride: Subsampling stride (analysis doesn't need every pixel).

    Returns:
        Color names ordered by pixel share (colors below COLOR_MIN_SHARE and
        beyond MAX_COLORS are dropped).
    """
    sample = rgb_image[::stride, ::stride].astype(np.float32) / 255.0
    hue, sat, val = _rgb_to_hsv(sample)
    names = _classify_pixel_colors(hue, sat, val)
    counts = np.bincount(names.ravel(), minlength=len(COLOR_NAMES))
    total = counts.sum()
    ranked = np.argsort(counts)[::-1]
    return [
        COLOR_NAMES[i] for i in ranked
        if counts[i] / total >= COLOR_MIN_SHARE
    ][:MAX_COLORS]


def clutter_metrics(ranges: list[float], range_max: float = 3.5) -> dict:
    """Computes obstacle clutter statistics from a LIDAR scan.

    Args:
        ranges: Scan ranges in meters (inf/nan allowed).
        range_max: Sensor maximum range; readings beyond it count as open.

    Returns:
        {"obstacle_clusters": int, "near_fraction": float,
         "median_clearance_m": float, "clutter": "cluttered"|"moderate"|"open"}
    """
    arr = np.asarray(ranges, dtype=np.float64)
    finite = np.where(np.isfinite(arr), arr, range_max)
    finite = np.clip(finite, 0.0, range_max)

    near = finite < NEAR_OBSTACLE_M
    # Count groups of consecutive near-beams, allowing a range jump to split
    # a group (two objects at different depths within the near band).
    clusters = 0
    in_cluster = False
    prev = None
    for is_near, dist in zip(near, finite, strict=True):
        if is_near and (not in_cluster or (prev is not None and abs(dist - prev) > GAP_JUMP_M)):
            clusters += 1
            in_cluster = True
        elif not is_near:
            in_cluster = False
        prev = dist if is_near else None

    near_fraction = float(near.mean()) if len(finite) else 0.0
    median_clearance = float(np.median(finite)) if len(finite) else range_max

    if clusters >= 6 or near_fraction > 0.55:
        clutter = 'cluttered'
    elif clusters <= 2 and median_clearance > 2.2:
        clutter = 'open'
    else:
        clutter = 'moderate'

    return {
        'obstacle_clusters': clusters,
        'near_fraction': round(near_fraction, 2),
        'median_clearance_m': round(median_clearance, 2),
        'clutter': clutter,
    }


def describe_scene(rgb_image: np.ndarray | None, ranges: list[float] | None) -> dict:
    """Builds the textual scene description from camera and LIDAR data.

    Either input may be None (missing sensor); the description degrades
    gracefully.

    Returns:
        Dict with "colors", clutter metrics, and a "description" string ready
        to store in semantic memory (coordinates are prepended by the memory
        layer, not here).
    """
    colors = dominant_colors(rgb_image) if rgb_image is not None else []
    metrics = clutter_metrics(ranges) if ranges is not None else {
        'obstacle_clusters': 0, 'near_fraction': 0.0,
        'median_clearance_m': math.nan, 'clutter': 'unknown',
    }

    parts = []
    if colors:
        parts.append('predominantly ' + ' and '.join(colors))
    clutter = metrics['clutter']
    if clutter == 'cluttered':
        parts.append(
            f'a cluttered space with many objects '
            f'({metrics["obstacle_clusters"]} obstacle groups nearby)',
        )
    elif clutter == 'open':
        parts.append('an open, uncluttered space')
    elif clutter == 'moderate':
        parts.append(
            f'a moderately furnished space '
            f'({metrics["obstacle_clusters"]} obstacle groups nearby)',
        )

    return {
        'colors': colors,
        **metrics,
        'description': ', '.join(parts) if parts else 'no sensor data available',
    }
