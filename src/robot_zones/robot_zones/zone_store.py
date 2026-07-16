"""SQLite-backed storage for user-defined navigation zones, shared across nodes.

Plain sqlite3 (stdlib) rather than a ROS service: dashboard_node (writer),
skills_executor_node, and llm_planner_node (readers) all need low-latency
access to the same small table, and a short-lived connection per call avoids
any cross-thread/cross-process locking concerns for this write volume.
"""

import sqlite3
import time
from pathlib import Path


class ZoneStore:
    """CRUD for named rectangular zones persisted in a shared SQLite file.

    Args:
        db_path: Path to the SQLite database file (created if missing).
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS zones (
                    name TEXT PRIMARY KEY,
                    x_min REAL NOT NULL,
                    y_min REAL NOT NULL,
                    x_max REAL NOT NULL,
                    y_max REAL NOT NULL,
                    created_at REAL NOT NULL
                )
                """,
            )

    def load_all(self) -> dict:
        """Returns all zones as {name: {x_min, y_min, x_max, y_max}}."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT name, x_min, y_min, x_max, y_max FROM zones ORDER BY name',
            ).fetchall()
        return {
            name: {'x_min': x0, 'y_min': y0, 'x_max': x1, 'y_max': y1}
            for name, x0, y0, x1, y1 in rows
        }

    def save(self, name: str, area: dict) -> None:
        """Inserts or replaces a zone.

        Args:
            name: Zone name, e.g. "cocina".
            area: {x_min, y_min, x_max, y_max} in map-frame meters.
        """
        with self._connect() as conn:
            conn.execute(
                'INSERT OR REPLACE INTO zones (name, x_min, y_min, x_max, y_max, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    name, area['x_min'], area['y_min'], area['x_max'], area['y_max'],
                    time.time(),
                ),
            )

    def delete(self, name: str) -> bool:
        """Removes a zone. Returns False if it did not exist."""
        with self._connect() as conn:
            cursor = conn.execute('DELETE FROM zones WHERE name = ?', (name,))
            return cursor.rowcount > 0
