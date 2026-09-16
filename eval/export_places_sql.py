"""Copies the robot's place memory into SQLite for the LLM → SQL condition (ADR-033).

The experiment compares two ways of looking up the *same* places: vector
similarity over semantic_map (RAG) and a query the LLM writes over a table. So
the table must hold exactly what semantic_map holds for the active map session,
taken right before the SQL condition runs, after seeding and exploration.

Every document "<label> at (x=…, y=…) in <zone>: <description>" becomes one row
of places(name, x, y, zone, description) in $ROBOT_WS/data/places.db — the
default `places_db` of llm_planner_node.

Usage (stack running, memory seeded):
    python3 export_places_sql.py            # prints how many rows were written
"""

import os
from pathlib import Path

import rclpy

from robot_brain.sql_memory import write_places_db

from bench_lib import BenchNode, spin_in_thread

HERE = Path(__file__).resolve().parent
WS_ROOT = Path(os.environ.get('ROBOT_WS', HERE.parent))
PLACES_DB = WS_ROOT / 'data' / 'places.db'


def main():
    """Exports the active session's semantic_map to places.db."""
    rclpy.init()
    node = BenchNode()
    spin = spin_in_thread(node)
    entries = node.inspect_memory('semantic_map')
    written = write_places_db(PLACES_DB, [entry['document'] for entry in entries])
    node.get_logger().info(f'{written} of {len(entries)} memories written to {PLACES_DB}')
    spin.stop()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
