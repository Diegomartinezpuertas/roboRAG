"""Map-session identity for the semantic memory — pure logic, no ROS imports.

The robot's coordinate memories (semantic_map, task_history) store *absolute*
map-frame poses. Those are only meaningful relative to the SLAM map that was
live when they were written, and this sim rebuilds its map from scratch on every
launch — so a pose logged in one session points somewhere else in the next
(see docs/rag-pipeline.md §6, and ADR-019).

A **map session** is a stable identifier for "the map these coordinates belong
to". Every coordinate memory is tagged with the current session id; retrieval
of those collections is filtered to the active id, so memories from a dead map
are simply not returned. Saving the SLAM map preserves the pairing (map ↔ id);
reloading that map restores the id, and the old memories become valid again.

The id lives in `<maps_dir>/session.json`. It is created on first use, rotated
when a fresh map is started, and pinned when a saved map is loaded.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class MapSession:
    """Tracks the active map-session id, persisted in `<maps_dir>/session.json`.

    Args:
        maps_dir: Directory holding session.json and any saved maps.
    """

    def __init__(self, maps_dir: str | Path) -> None:
        self._dir = Path(maps_dir)
        self._path = self._dir / 'session.json'

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, map_id: str) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({'map_id': map_id, 'updated_at': time.time()}, indent=2),
            encoding='utf-8',
        )
        return map_id

    def current_id(self) -> str:
        """Returns the active map-session id, creating a fresh one if none exists.

        Returns:
            The current map-session id (a short uuid, or a name set by set_id).
        """
        existing = self._read().get('map_id')
        return existing if existing else self._write(_fresh_id())

    def rotate(self) -> str:
        """Starts a new session: generates and persists a fresh id.

        Call this when a new SLAM map is being built from scratch, so memories
        written against the previous map stop being retrieved.

        Returns:
            The new map-session id.
        """
        return self._write(_fresh_id())

    def set_id(self, map_id: str) -> str:
        """Pins the session to a specific id — used when loading a saved map.

        Args:
            map_id: The saved map's id, so its coordinate memories match again.

        Returns:
            The id that was set.
        """
        return self._write(map_id)


def read_active_map_id(maps_dir: str | Path) -> str:
    """Reads the active map-session id without creating one.

    A read-only helper for producers outside rag_node (e.g. report_skill
    stamping a task log) that must not mint a session of their own. Returns ''
    when no session exists yet or the file is unreadable — an untagged memory,
    which the coordinate filter treats as "not this map".

    Args:
        maps_dir: Directory holding session.json.

    Returns:
        The active map-session id, or '' if none is set.
    """
    try:
        data = json.loads((Path(maps_dir) / 'session.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ''
    return data.get('map_id', '')


def saved_map_stamp(map_base: str | Path) -> int | None:
    """Identifies the version of a saved map on disk, to tell whether a save wrote it.

    SLAM Toolbox's serialize service can answer without writing anything: in
    its localization mode it logs "Cannot call serialize map" and returns an
    untouched response, whose result code reads as success (ADR-035). Comparing
    this stamp before and after the call is the evidence that does not depend on
    that code.

    Args:
        map_base: Base path of the map, without extension (<maps_dir>/<id>/map).

    Returns:
        The pose graph's modification time in nanoseconds, or None unless both
        map.posegraph and map.data exist.
    """
    base = Path(map_base)
    posegraph, data = base.with_suffix('.posegraph'), base.with_suffix('.data')
    try:
        stamp = posegraph.stat().st_mtime_ns
        data.stat()
    except OSError:
        return None
    return stamp


def map_image_stamp(map_base: str | Path) -> int | None:
    """Identifies the version of a saved map's occupancy image, to tell whether a save wrote it.

    The image (map.yaml + map.pgm, written by SLAM Toolbox's map saver) is what
    map_server loads when a saved map is opened read-only (ADR-035).

    Args:
        map_base: Base path of the map, without extension (<maps_dir>/<id>/map).

    Returns:
        The newer modification time of the two files in nanoseconds, or None
        unless both exist.
    """
    base = Path(map_base)
    try:
        return max(base.with_suffix('.yaml').stat().st_mtime_ns,
                   base.with_suffix('.pgm').stat().st_mtime_ns)
    except OSError:
        return None


def _fresh_id() -> str:
    return uuid.uuid4().hex[:12]
