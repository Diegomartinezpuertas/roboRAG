# ADR-019: Map-session versioning for coordinate memories, and SLAM map persistence

**Date:** 2026-07-21
**Status:** Accepted — extended by
[ADR-026](ADR-026-shipped-map-and-demo-launch.md) (the `saved_map:=<id>` launch argument this ADR left as follow-up)

## Context

The semantic memory stores **absolute** map-frame coordinates: `semantic_map`
embeds them in each scene document, and `task_history` outcomes often quote them
("moved to the base at (x=-0.30, y=-1.06)"). Those coordinates only mean
anything relative to the SLAM map that was live when they were written.

This sim builds its map from scratch on every launch — there was no save/load —
so **a pose written in one session points somewhere else in the next.**
Retrieval has no way to know that. Running the benchmark end-to-end surfaced the
consequence: a task log from an earlier session, carrying a coordinate from a
dead map, was retrieved for "go to the base" and the planner navigated to it,
over the correct freshly-observed entry sitting next to it in `semantic_map`.
The `zone_nav` control failed in both conditions for a reason that had nothing
to do with RAG (see the story in [rag-pipeline.md §6](../rag-pipeline.md)).

It is more than a benchmark nuisance: storing absolute coordinates in a memory
that outlives the map is a latent **correctness** bug in the running system.

## Decision

A **map session** is a stable id for "the map these coordinates belong to". Two
mechanisms, one for correctness and one for persistence.

### 1. Version every coordinate memory by map session (the correctness fix)

- `MapSession` (`robot_rag/map_session.py`, pure stdlib) owns a small id
  persisted in `data/maps/session.json`. It is created on first use, `rotate()`d
  when a fresh map is started, and `set_id()`-pinned when a saved map is loaded.
- `rag_node` reads the active id at startup and **tags every write** with it:
  `semantic_map` upserts and `task_history` ingests carry `map_id` in metadata.
  `report_skill` stamps the active id into each task log, so the memory a log
  becomes is scoped to the map the task actually ran on.
- **Retrieval of the coordinate collections is filtered to the active id**
  (`ChromaManager.query(where={'map_id': ...})`). `semantic_map` and
  `task_history` are scoped; `knowledge_base` holds no coordinates and is never
  filtered. A memory from a different — or unknown, pre-versioning — map simply
  is not returned.

The upshot: a stale coordinate cannot resurface as current. Verified live —
a scene written under session A is retrieved under A and invisible under a
rotated session B, while ChromaDB still physically holds it.

### 2. Persist and reload the SLAM map (so memories survive on purpose)

Versioning alone makes a fresh map discard old memories. To *keep* them across a
restart, the map itself must persist so its id keeps meaning the same geometry:

- A `save_map` maintenance skill serializes the live map via SLAM Toolbox's
  `/slam_toolbox/serialize_map` to `data/maps/<map_id>/map.{posegraph,data}`,
  under the active session id. It is **deliberately not in the planner's prompt
  or `toolkit.VALID_SKILLS`**, so the LLM never emits it; it is reached only by
  a direct `ros2 service call /skills/execute`.
- Reloading: launch SLAM Toolbox with `map_file_name` pointing at the saved
  `.posegraph` (it deserializes and continues in the same `map` frame), and pass
  `map_session_id:=<map_id>` to `rag_node`. The session is re-pinned to that id,
  and its coordinate memories are valid again.

## Rationale

**Why filter rather than delete stale memories?** Deletion is destructive and
irreversible; a saved-and-reloaded map should bring its memories back. Filtering
by session id keeps every memory, and simply scopes what is *visible now*.

**Why tag in metadata, not in the document text?** The coordinates are already
in the text (ADR-012, so the planner can read them). The `map_id` is a retrieval
concern, not something the planner should see — metadata with a `where` filter
is exactly ChromaDB's mechanism for that.

**Why `save_map` is a hidden maintenance skill.** It is an operator action
("checkpoint this map"), not a step in answering a user goal. Putting it in the
prompt would invite the LLM to emit it; keeping it out of `VALID_SKILLS` means a
hallucinated `save_map` in a plan is still rejected. Dispatch-only is the
smallest surface that works.

**Why not switch to AMCL over a static saved map?** That would contradict
[ADR-004](ADR-004-slam-toolbox-no-amcl.md) (live SLAM, the robot maps unknown
space). SLAM Toolbox's own serialize/deserialize keeps the live-mapping model
*and* persists the map — the right tool, no localization-stack change.

**Alternative rejected — a per-session ChromaDB.** A fresh vector store per map
would isolate coordinates too, but it throws away the static `knowledge_base`
and any cross-map episodic value, and turns "reload a map" into "rebuild an
index". One store with a metadata filter is cheaper and keeps knowledge shared.

## Consequences

- The stale-coordinate bug is fixed at the source: retrieval never returns a
  pose from a map that is not the active one. The benchmark's clean-state
  requirement (empty `data/logs`, `data/chroma_db`) is now a convenience, not a
  correctness crutch — a leftover log is scoped out, not acted on.
- `data/maps/` is runtime state (session id + serialized maps), gitignored
  alongside `data/chroma_db` and `data/logs`.
- `robot_skills` gains a dependency on `robot_rag` for the read-only
  `read_active_map_id` helper — the coupling already existed at runtime (skills
  call `/rag/update_map`); this shares one definition of the session file format
  instead of duplicating it.
- New parameters: `rag_node.maps_dir`, `rag_node.map_session_id`,
  `skills_executor_node.maps_dir` (all `ROBOT_WS`-derived, ADR-015).
- Memories written before this change carry no `map_id` and are treated as an
  unknown map — invisible to any tagged session. That is the safe default; they
  can be re-collected by exploring.
- The save/reload *orchestration* (a single `saved_map:=<id>` launch arg wiring
  both SLAM and rag_node) is left as a documented two-parameter procedure rather
  than a bespoke conditional in the launch files. The pieces — serialize skill,
  `map_file_name`, `map_session_id` — are all in place and unit- and
  live-tested; the convenience wrapper is a small follow-up. *(Done in
  [ADR-026](ADR-026-shipped-map-and-demo-launch.md).)*
