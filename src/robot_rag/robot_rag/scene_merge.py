"""When two observations of a place are the same memory — pure logic, no ROS imports.

Exploring stores a description of every spot the robot reaches (ADR-014). The
first version keyed each memory to the pose rounded to a 0.5 m grid, so
re-visiting the *same half-metre* updated it — but a room is many half-metres,
and every frontier the robot reached inside it became another memory saying
the same thing. On a real run: 28 scene memories, 12 distinct descriptions,
one of them eleven times ("predominantly gray and brown, an open, uncluttered
space"). Retrieval then fills its top-k with copies of one place and crowds out
everything else, and the memory viewer reads as noise.

This module decides when a new observation is a *re-observation*:

* the same **group key** — what the scene descriptor measured, normalized:
  the set of dominant colors (order-free: "gray and brown" and "brown and gray"
  are one look, the order only flips on a few pixels) plus the clutter class
  (the obstacle count is sensor noise and is left out);
* in the same **map session** and the same **zone** — a kitchen and the
  living room next door never merge, however alike they look;
* within a **merge radius** of an existing memory's anchor.

The anchor pose is the first observation's and never moves. If it followed
each new observation, a long corridor of one colour would drag a single memory
along with the robot and lose the places it had been. Both paths below — live
merging and the compaction of old data — keep one invariant: no two memories
of the same look, zone and map session lie within one radius of each other.

It also carries the one-off migration for memories written before group keys
existed (`legacy_group_key`, `plan_compaction`), used by `compact_memory`.

See docs/decisions/ADR-025-scene-memory-merging.md.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Default merge radius, in meters. A room of the TurtleBot3 house is ~3-4 m
# across, so 2 m collapses one room's look to one or two memories while two
# spots far apart in a large open area still stay distinct places.
DEFAULT_MERGE_RADIUS_M = 2.0

# Seeded benchmark fixtures (eval/seed_memory.py) carry this id prefix. They are
# placed on purpose at fixed coordinates and must never be merged or compacted.
SEED_ID_PREFIX = 'scene-seed-'


def scene_group_key(colors: list[str], clutter: str) -> str:
    """Builds the key under which observations of the same look are grouped.

    Args:
        colors: Dominant color names from the scene descriptor, in any order.
        clutter: Clutter class: "open" | "moderate" | "cluttered" | "unknown".

    Returns:
        A stable string such as "colors=brown+gray;clutter=open".
    """
    palette = '+'.join(sorted({c.strip().lower() for c in colors if c.strip()})) or 'none'
    return f'colors={palette};clutter={(clutter or "unknown").strip().lower()}'


def find_merge_target(
    candidates: list[dict], x: float, y: float, radius: float,
) -> dict | None:
    """Finds the existing memory a new observation at (x, y) should fold into.

    The caller has already filtered candidates to the same group key, map
    session and zone; what is left to decide is distance.

    Args:
        candidates: Stored entries as {id, document, metadata}, with the anchor
            pose in metadata pose_x/pose_y.
        x: New observation's x in map-frame meters.
        y: New observation's y in map-frame meters.
        radius: Merge radius in meters; <= 0 disables merging.

    Returns:
        The nearest candidate whose anchor lies within the radius, or None.
    """
    if radius <= 0:
        return None
    best, best_dist = None, math.inf
    for entry in candidates:
        if str(entry.get('id', '')).startswith(SEED_ID_PREFIX):
            continue
        metadata = entry.get('metadata') or {}
        ax, ay = metadata.get('pose_x'), metadata.get('pose_y')
        if not isinstance(ax, (int, float)) or not isinstance(ay, (int, float)):
            continue
        dist = math.hypot(ax - x, ay - y)
        if dist <= radius and dist < best_dist:
            best, best_dist = entry, dist
    return best


def merged_observations(existing_metadata: dict) -> int:
    """Returns the observation count after folding one more observation in.

    Memories written before merging existed carry no count; they had been seen
    once.
    """
    previous = existing_metadata.get('observations', 1)
    return (previous if isinstance(previous, int) and previous > 0 else 1) + 1


# --- migration of memories written before group keys existed ---------------

_COLORS_RE = re.compile(r'predominantly ([a-z]+(?: and [a-z]+)*)')


def legacy_group_key(description: str) -> str | None:
    """Recovers the group key from a scene description stored without one.

    Reads the sentence scene_descriptor has always written ("predominantly X and
    Y, <clutter phrase>"). This is a migration of existing data, not a runtime
    path: new observations carry their key from the structured descriptor
    output and are never parsed.

    Args:
        description: Stored document or description text.

    Returns:
        The group key, or None when the text is not a scene description.
    """
    colors_match = _COLORS_RE.search(description)
    if 'cluttered space with many objects' in description:
        clutter = 'cluttered'
    elif 'moderately furnished' in description:
        clutter = 'moderate'
    elif 'open, uncluttered' in description:
        clutter = 'open'
    elif colors_match is None:
        return None
    else:
        clutter = 'unknown'
    colors = colors_match.group(1).split(' and ') if colors_match else []
    return scene_group_key(colors, clutter)


@dataclass
class CompactionGroup:
    """One place that was stored several times, and what to keep of it.

    Attributes:
        keep_id: Entry that survives — the member nearest the group's mean
            position, so the kept pose is one the robot actually reached and
            the most central of them.
        drop_ids: Duplicates to delete.
        group_key: Key recovered for the group, written onto the kept entry.
        observations: Total observations the kept entry now stands for.
    """

    keep_id: str
    drop_ids: list[str] = field(default_factory=list)
    group_key: str = ''
    observations: int = 1


def plan_compaction(entries: list[dict], radius: float) -> list[CompactionGroup]:
    """Plans how to collapse stored scene memories into one per place and look.

    Only scene memories (id "scene-…", seeded fixtures excluded) take part, and
    groups never span map sessions or zones. The result satisfies the same
    invariant merge-on-write maintains live: **no two surviving memories of the
    same (map session, zone, look) lie within `radius` of each other.** That is
    what makes the plan idempotent — compacting its output changes nothing, and
    the next live observation merges exactly as it would have on a store that
    never had duplicates.

    Clusters are merged repeatedly until a full pass finds no two survivors
    within `radius`; each cluster keeps the member nearest its mean position,
    so the surviving pose is one the robot actually reached.

    Args:
        entries: All semantic_map entries as {id, document, metadata}.
        radius: Merge radius in meters.

    Returns:
        One CompactionGroup per cluster with more than one member, ordered by
        kept id. Singletons need no change and are not returned.
    """
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for entry in entries:
        entry_id = str(entry.get('id', ''))
        if not entry_id.startswith('scene-') or entry_id.startswith(SEED_ID_PREFIX):
            continue
        metadata = entry.get('metadata') or {}
        if not isinstance(metadata.get('pose_x'), (int, float)):
            continue
        key = metadata.get('group_key') or legacy_group_key(entry.get('document') or '')
        if key is None:
            continue
        bucket = (str(metadata.get('map_id') or ''), str(metadata.get('room_zone') or ''), key)
        buckets.setdefault(bucket, []).append(entry)

    groups: list[CompactionGroup] = []
    for (_map_id, _zone, key), members in buckets.items():
        clusters = [[entry] for entry in sorted(members, key=lambda e: str(e['id']))]
        while True:
            merged: list[list[dict]] = []
            for cluster in clusters:
                keeper = _medoid(cluster)
                for target in merged:
                    if _distance(_medoid(target), keeper) <= radius:
                        target.extend(cluster)
                        break
                else:
                    merged.append(list(cluster))
            if len(merged) == len(clusters):
                break
            clusters = merged
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep = _medoid(cluster)
            groups.append(CompactionGroup(
                keep_id=str(keep['id']),
                drop_ids=sorted(str(e['id']) for e in cluster if e is not keep),
                group_key=key,
                observations=sum(_observations(e['metadata']) for e in cluster),
            ))
    return sorted(groups, key=lambda g: g.keep_id)


def _medoid(cluster: list[dict]) -> dict:
    """The member nearest the cluster's mean position (ties broken by id)."""
    mean_x = sum(e['metadata']['pose_x'] for e in cluster) / len(cluster)
    mean_y = sum(e['metadata']['pose_y'] for e in cluster) / len(cluster)
    return min(cluster, key=lambda e: (
        math.hypot(e['metadata']['pose_x'] - mean_x, e['metadata']['pose_y'] - mean_y),
        str(e['id']),
    ))


def _distance(a: dict, b: dict) -> float:
    """Planar distance between two entries' anchor poses."""
    return math.hypot(
        a['metadata']['pose_x'] - b['metadata']['pose_x'],
        a['metadata']['pose_y'] - b['metadata']['pose_y'],
    )


def _observations(metadata: dict) -> int:
    """Observation count an entry stands for (1 for memories that predate counting)."""
    count = metadata.get('observations', 1)
    return count if isinstance(count, int) and count > 0 else 1
