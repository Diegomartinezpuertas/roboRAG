# ADR-028: A fresh map's memory session is named after its frame

**Date:** 2026-09-16
**Status:** Accepted

## Context

ADR-019 scopes coordinate memories to a *map session* so a pose from one map is
never retrieved as current on another. It defined `MapSession.rotate()` for "a
new SLAM map is being built from scratch" — and nothing ever called it. Every
launch without a saved map simply **continued the last persisted id**: memories
written against any earlier fresh map stayed active.

That worked by accident. The simulation always spawns the robot at the same pose
in the same world, and SLAM puts a fresh map's origin at the spawn pose, so
coordinates from one fresh map happen to match the next. Spawn the robot
elsewhere, or load another world, and every old memory would point at the wrong
place while still being retrieved — exactly the bug ADR-019 exists to prevent.

ADR-026's `saved_map:=<id>` made the gap worse: loading a saved map re-pins the
persisted session, and the *next* fresh-map launch inherited that saved map's id.

## Decision

`full_system.launch.py` always pins rag_node's session explicitly:

- with `saved_map:=<id>` → the saved map's id (ADR-026, unchanged);
- otherwise → **the id of the fresh map's frame**:
  `fresh_<world>_x<spawn x>_y<spawn y>`, e.g.
  `fresh_turtlebot3_house_x-2.00_y-0.50`.

A fresh map's frame *is* (world, spawn pose), so fresh maps of the same frame
share memories — they share coordinates — and a different world or spawn pose
never sees them. The id is deterministic and uses only `[\w.-]`, so it is also a
valid saved-map id (`save_map` without a name saves under the session id).

`agent.launch.py` takes the session as `memory_session` (was `saved_map`, added
the same day in ADR-026). Empty keeps the persisted session — what an agent-only
run, such as the offline benchmark (ADR-020), wants.

The world name and spawn defaults live once, in `robot_bringup/saved_maps.py`
(`DEFAULT_WORLD`, `DEFAULT_SPAWN_X/Y`), used by both the simulation launch and
the session id.

**Existing memories** carry whatever id was persisted before (e.g. `provesess`).
`compact_memory --retag-session OLD NEW` moves a session's `semantic_map`
memories to another id — dry run by default, backup before writing. It is never
done implicitly: retagging memories into a frame they were not recorded in would
make their coordinates lie.

## Rationale

**Why not call `rotate()` on every fresh launch.** Correct for an arbitrary
robot, and it would throw away every memory at every launch in a simulation
whose frame never changes — making the self-built memory (ADR-014) useless across
sessions for no safety gain.

**Why not keep "continue the persisted id".** It is safe only while nobody
changes the spawn pose or world, and nothing enforces that. A named frame makes
the assumption explicit and checked.

**Why `task_history` is not retagged.** It is rebuilt from `data/logs` on every
start, each log carrying the id it ran under (ADR-019); retagged entries would
revert at the next launch.

## Consequences

- A launch prints `Memory session: <id>`, and rag_node logs the same id: which
  memories are live is no longer implicit.
- Memories from before this change stay under their old id until retagged; the
  memory viewer shows them as "de otro mapa" with the scope filter off.
- Changing `x_pose`/`y_pose` now starts an empty session for that frame, as it
  should.
- Layer-1 tests pin the id format, per-frame sharing, validity as a map id, and
  the saved-map override.
