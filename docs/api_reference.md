# API Reference

## Messages

### `SemanticObject.msg`

| Field | Type | Description |
|-------|------|-------------|
| object_id | string | Unique identifier for the detected object |
| label | string | Object class, e.g. "chair", "door", "bottle" |
| confidence | float32 | Detection confidence [0.0, 1.0] |
| pose | geometry_msgs/Pose | 2D/3D pose in the map frame |
| description | string | Free-text description (scene descriptor output or user text) |
| room_zone | string | Semantic zone, e.g. "kitchen", "corridor" |
| timestamp | builtin_interfaces/Time | Last observed |
| group_key | string | Observations sharing it within `scene_merge_radius` (same map session and zone) are one memory; empty = never merged ([ADR-025](decisions/ADR-025-scene-memory-merging.md)) |

## Services

### `/rag/query` (QueryRAG.srv)

Retrieves relevant context from ChromaDB for a natural-language query.

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| query_text | string | Natural-language query |
| collection_name | string | "semantic_map" \| "knowledge_base" \| "task_history" |
| top_k | int32 | Number of results (default: 5) |

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| contexts | string[] | Retrieved fragments, ordered by similarity |
| scores | float32[] | Cosine similarity scores [0.0, 1.0] |
| success | bool | False if the query failed or the collection is unknown |
| error_msg | string | Empty when success=True |

Served by `rag_node`. Note: only document *text* is returned (no metadata),
which is why stored objects embed their coordinates in the text (ADR-012).

### `/rag/inspect` (InspectMemory.srv)

Browses or searches a collection **with ids, metadata and scores** — the
read-only view behind the dashboard's memory panel
([ADR-024](decisions/ADR-024-memory-inspection-service.md)). Unlike
`/rag/query`, which serves the planner, it can list a collection without a query
(and therefore without embedding anything), and it lets the caller decide
whether to apply the map-session filter.

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| collection_name | string | "semantic_map" \| "knowledge_base" \| "task_history" |
| query_text | string | Natural-language query; empty browses the entries as stored |
| limit | int32 | Max entries to return (default: 50, capped at 200) |
| active_map_only | bool | Scope coordinate collections to the active map session ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)) |

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| items_json | string | JSON array of `{id, document, metadata, score}`; `score` is `-1.0` when browsing |
| stats_json | string | JSON object `{collection_name: document_count}` for every served collection |
| map_id | string | Active map-session id, so a client can flag entries from other maps |
| success | bool | False if the collection is unknown or the query failed |
| error_msg | string | Empty when success=True |

Served by `rag_node`.

### `/rag/delete` (DeleteMemory.srv)

Deletes memories by id — the memory viewer's per-card delete
([ADR-030](decisions/ADR-030-dashboard-redesign-and-browser-tests.md)).

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| collection_name | string | Only `semantic_map` accepts deletions |
| ids | string[] | Entry ids to delete; ids that do not exist are ignored |

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| deleted | int32 | How many of the ids existed and were removed |
| success | bool | False if the collection refuses deletions or the store failed |
| error_msg | string | Empty when success=True |

Served by `rag_node`. `knowledge_base` and `task_history` refuse: they are
rebuilt from files on disk, so a deletion would either not stick
(`task_history`, re-ingested every start) or not be undoable.

### `/rag/update_map` (UpdateMap.srv)

Inserts or updates a detected object in the `semantic_map` collection.

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| object_data | robot_interfaces/SemanticObject | Object to upsert |

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| success | bool | False if the update failed |
| error_msg | string | Empty when success=True |
| object_id | string | Id the object was stored under — an existing memory's id when merged |
| merged | bool | True if folded into an existing memory instead of creating one |

Served by `rag_node`. Called by `skills_executor_node` after each `perceive`,
and by `dashboard_node` when a zone is created and — once per start, as soon
as the service answers — for every stored zone, so zones are always in the
active memory session ([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)). An object with a `group_key`
is merged into the nearest memory of the same key, map session and zone within
`scene_merge_radius`: that memory keeps its id and anchor pose, its description
is refreshed and its `observations` count raised
([ADR-025](decisions/ADR-025-scene-memory-merging.md)).

### `/skills/execute` (ExecuteSkill.srv)

Dispatches a skill by name with JSON-encoded parameters.

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| skill_name | string | "navigate" \| "explore" \| "perceive" \| "scan_360" \| "report" \| "save_map" † |
| params_json | string | JSON-encoded parameters |

**Response:**
| Field | Type | Description |
|-------|------|-------------|
| result_json | string | JSON-encoded result |
| success | bool | False if the skill failed |
| error_msg | string | Empty when success=True |

Served by `skills_executor_node`.

**Per-skill parameters:**

| skill_name | params_json | result_json |
|---|---|---|
| navigate | `{"zone": "kitchen"}` or `{"x": 1.0, "y": 0.5, "theta": 0.0}` | `{"reached": bool, "message": str}` |
| explore | `{"duration_sec": 30, "zone": "kitchen" (optional)}` | `{"visited_frontiers": int, "message": str}` |
| perceive | `{"zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "zone": str, "object_id": str, "stored": bool, "merged": bool}` |
| scan_360 | `{"steps": 8 (optional, 4-16), "zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "zone": str, "object_id": str, "headings_completed": int, "stored": bool, "merged": bool}` |
| report | `{"message": "...", "goal_text": "..."}` | `{"published": bool}` |
| save_map † | `{"name": "home" (optional)}` | `{"map_id": str, "path": str, "message": str}` |

Zone names are resolved against the SQLite zone store; unknown zones fail
with an explicit error (no hardcoded fallbacks). `zone` in a perceive/scan_360
result is the zone the observation was made in (empty outside every zone); when
that zone's name denotes a kind of room, what the room is for is appended to the
stored description ([ADR-022](decisions/ADR-022-room-semantics.md)).

† `save_map` is a **maintenance** skill: it serializes the live SLAM map (via
SLAM Toolbox) under `name`, or under the active map-session id when no name is
given, for persistence and memory versioning
([ADR-019](decisions/ADR-019-map-session-memory-versioning.md),
[ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)). It is deliberately
absent from the planner's prompt and from `toolkit.VALID_SKILLS`, so the LLM
never emits it — it is reached through the dashboard's "Guardar mapa"
(`POST /api/map/save`) or a direct `ros2 service call`.

## Topics

| Topic | Type | Published by | Consumed by |
|---|---|---|---|
| /robot/goal | std_msgs/String | user (CLI / dashboard) | llm_planner_node |
| /robot/status | std_msgs/String | llm_planner_node | user / dashboard |
| /robot/plan | std_msgs/String (plan JSON, as it will run; `raw_steps` + `plan_corrections` when the plan check replaced a step, [ADR-032](decisions/ADR-032-plan-check-before-execution.md)) | llm_planner_node | eval harness |
| /robot/response | std_msgs/String | skills_executor_node (report) | user / dashboard |
| /camera/image_raw | sensor_msgs/Image | Gazebo bridge | skills_executor_node |
| /scan | sensor_msgs/LaserScan | Gazebo bridge | skills_executor_node (clutter metrics) |
| /map | nav_msgs/OccupancyGrid | slam_toolbox | skills_executor_node, dashboard_node |
| /cmd_vel | geometry_msgs/TwistStamped | cmd_vel_mux_node — the only intended publisher ([ADR-029](decisions/ADR-029-cmd-vel-mux.md))† | Gazebo bridge |
| /robot/cmd_vel_manual | geometry_msgs/TwistStamped | dashboard_node (WASD, [ADR-023](decisions/ADR-023-browser-teleop.md)) | cmd_vel_mux_node (priority 2) |
| /cmd_vel_nav_out | geometry_msgs/TwistStamped | Nav2 collision_monitor | cmd_vel_mux_node (priority 1) |
| /robot/cmd_vel_source | std_msgs/String (transient local) | cmd_vel_mux_node: `teleop` \| `nav2` \| `idle` | dashboard_node (drive panel) |
| /clock | rosgraph_msgs/Clock | Gazebo bridge | every node (sim time); dashboard_node also estimates the real-time factor |
| /rosout | rcl_interfaces/Log | every node (ROS 2 logging) | dashboard_node (log viewer) |

† Nav2's `docking_server` also publishes `/cmd_vel` directly (nav2_bringup does
not remap it), but only during a docking action, which this project never
requests.

## Dashboard HTTP (robot_dashboard)

Served by `dashboard_node` at `http://localhost:8080` (`http_host`/`http_port`
parameters). The server binds loopback only: these endpoints publish goals to
the robot — and, since the manual-driving endpoints, *move* it — and carry no
authentication. Under WSL2 it stays reachable from a
Windows browser anyway (the localhost relay forwards into the VM's loopback);
set `http_host: "0.0.0.0"` in `agent_params.yaml` only to reach it from
another machine, and put it behind something that authenticates if you do.

### `GET /`

Single-page dashboard: planning timeline, ROS logs, interactive map (with WASD
manual driving), memory browser, text/voice goal input.

### `GET /api/events?since=<id>`

Events with id greater than `since`. A `since` ahead of the buffer — a page left
open across a relaunch, whose cursor belongs to the previous process — returns
everything instead of nothing, so the thread recovers without a reload.

| Field | Type | Description |
|-------|------|-------------|
| events | object[] | `{id, ts, type, text, source, level}`; type ∈ goal\|status\|response\|rosout |

### `GET /api/map`

Map geometry, robot pose, zones and simulation status. Light enough to poll
every second; the image itself comes from `/api/map/png`.

| Field | Type | Description |
|-------|------|-------------|
| map | object\|null | `{stamp, version, resolution, origin_x, origin_y, width, height}` — `stamp` is the grid's timestamp (ns of *simulation* time); `version` is `<server process id>-<stamp>`, the key for `/api/map/png` |
| robot | object\|null | `{x, y, yaw}` in the map frame (TF map→base_link); `yaw` in radians, counter-clockwise from +x |
| zones | object | `{name: {x_min, y_min, x_max, y_max}}` |
| sim | object | `{rtf, driver}`: real-time factor estimated from `/clock` (`null` when unknown or the clock is silent) and who holds `/cmd_vel` from `/robot/cmd_vel_source` (`teleop` \| `nav2` \| `idle`, `null` without a mux) |

### `GET /api/map/png`

The rendered SLAM map as `image/png` (top row = y_max), drawn as a blueprint:
walls in chalk, explored floor in plan blue, unexplored space as the page
colour. Versioned with an `ETag` equal to the map `version` and
`Cache-Control: no-cache`, so a client that sends `If-None-Match` gets
`304 Not Modified` while SLAM has not published a newer grid. The version
includes a per-process id because the stamp alone is simulation time, which
restarts at zero every launch: keyed on it, a relaunched dashboard served a
previous run's cached image as current
([ADR-030](decisions/ADR-030-dashboard-redesign-and-browser-tests.md)). `404`
before the first map arrives.

### `POST /api/goal`

Publishes a goal on `/robot/goal`.

**Body:** `{"text": "Go to the kitchen..."}` → `{"ok": true}` (400 if empty).

### `POST /api/zones` / `DELETE /api/zones/{name}`

Creates (and indexes into semantic memory) or deletes a named zone. See
[ADR-011](decisions/ADR-011-rag-quality-zones-sqlite.md).

**POST body:** `{"name": "kitchen", "x_min": ..., "y_min": ..., "x_max": ..., "y_max": ...}`

**POST response:** `{"ok": true, "name": "cocina", "room_type": "kitchen"}` —
`room_type` is the kind of room the name was recognized as, or `""` when the
name denotes none ([ADR-022](decisions/ADR-022-room-semantics.md)). A recognized
room is indexed with what that room is *for*, so it can be retrieved by function
("ve donde se suele cocinar") and not only by name.

### `POST /api/zones/here`

Names the room the robot is standing in: a square zone centered on the current
pose, stored and indexed exactly like a dragged rectangle. The companion of
manual driving — drive in, name it, carry on.

**Body:** `{"name": "cocina", "size": 2.0}` (size in meters, floor 0.4)
→ `{"ok": true, "name": "cocina", "area": {...}, "room_type": "kitchen"}`.
`400` if the name is blank, `409` if there is no `map→base_link` transform yet.

### `GET /api/room-types`

The room names the classifier understands, offered as autocomplete when naming a
zone: `{"names": ["cocina", "salón", "comedor", ...]}`.

### `GET /api/memory?collection=<name>&q=<query>&limit=<n>&active_only=<bool>`

The robot's RAG memory, flattened for display — served by `/rag/inspect`
([ADR-024](decisions/ADR-024-memory-inspection-service.md)). An empty `q`
browses the collection as stored (no embedding call); a non-empty `q` searches
it semantically. `limit` defaults to 50 and is clamped to [1, 200];
`active_only` (default `true`) scopes coordinate collections to the live map
session.

| Field | Type | Description |
|-------|------|-------------|
| ok | bool | False when the memory service is unavailable or the query failed |
| collection | string | Collection that was read |
| query | string | The query, trimmed |
| map_id | string | Active map-session id |
| stats | object | `{collection_name: document_count}` for every collection |
| entries | object[] | `{id, title, document, score, label, zone, source, x, y, map_id, observations, stale, count, ids, facts}` |
| error | string | Empty when ok |

`score` is `-1.0` while browsing, `x`/`y` are `null` for entries without a pose,
`stale` marks a coordinate memory written against a different map, and
`observations` is how many observations a merged memory stands for. Entries are
**grouped for display**: memories at the exact same spot, or with identical
documents, come back as one entry with `count`, all member `ids`, and each
member's `{title, document}` in `facts` — storage keeps them apart
([ADR-025](decisions/ADR-025-scene-memory-merging.md)). **Always
`200`**, including when `rag_node` is down: the dashboard starts before it, and
the panel shows the reason rather than failing the request.

### `POST /api/memory/delete`

Deletes the memories behind one viewer card through `/rag/delete`.

**Body:** `{"collection": "semantic_map", "ids": ["landmark-estacion_a", "scene-seed-estacion_a"]}`
→ `{"ok": true, "deleted": 2}`. A card can stand for several stored ids (grouped
for display), so all of them are sent. `400` with no ids, or with rag_node's
reason when it refuses (only `semantic_map` is deletable) or is unavailable.

### `POST /api/teleop` / `POST /api/teleop/stop`

Manual driving ([ADR-023](decisions/ADR-023-browser-teleop.md)). The browser
sends the keys it is holding, never a velocity; the node applies its own speed
parameters, restarts its deadman and publishes to `/robot/cmd_vel_manual`, where
`cmd_vel_mux_node` gives it priority over Nav2
([ADR-029](decisions/ADR-029-cmd-vel-mux.md)).

**Body:** `{"keys": ["w", "a"], "boost": false}`
→ `{"ok": true, "linear": 0.18, "angular": 1.0, "driving": true}`

Held keys must be re-sent (the UI does so every 150 ms). A command not refreshed
within `teleop_timeout_sec` expires and the robot is stopped. `/api/teleop/stop`
ends the session immediately; an empty `keys` list means the same thing.

### `POST /api/events/clear`

Empties the dashboard's event buffer — the **Limpiar** button beside the
thread's tabs — so a recording or a demo starts on a clean thread (the
**Recorrido** toggle over the plan does the same for the robot's trail, in the
browser only); event ids
keep increasing, so pages that already polled are not re-sent old events
([ADR-030](decisions/ADR-030-dashboard-redesign-and-browser-tests.md)).

**Body:** none → `{"ok": true, "cleared": <how many were dropped>}`.

### `POST /api/map/save`

Persists the live SLAM map through the `save_map` maintenance skill
([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)) — what makes a
manual mapping run worth doing.

**Body:** `{"name": "casa"}` (normalized like a zone name; empty saves under the
active session id) → `{"ok": true, "result": {"map_id": ..., "path": ...}}`, or
`503` with the reason when the agent stack is not running or SLAM wrote nothing.
The last happens on a map loaded read-only (`saved_map_mode:=localization`),
where there is no SLAM to serialize, and in SLAM Toolbox's own localization
mode, which answers the save with success but writes no file — so the skill
checks the files themselves
([ADR-035](decisions/ADR-035-saved-map-loads-read-only.md)). The map lands in
`$ROBOT_WS/data/maps/<map_id>/`; relaunch with `saved_map:=<map_id>` to start
from it ([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)).

## Launch arguments (robot_bringup)

| Launch file | Argument (default) | Meaning |
|---|---|---|
| `full_system` | `use_nav2` (true) | Nav2 alongside SLAM; `false` for a manual mapping run |
| `full_system` | `use_rviz` (true) | RViz with map, LIDAR and costmaps — Nav2's stock view; with `use_nav2:=false`, `robot_bringup/rviz/mapping.rviz`, the same view without the Nav2 panels |
| `full_system` | `use_gz_gui` (false) | Gazebo window — costs real-time factor on WSL2 (measured ~0.68 headless → ~0.59 with it; ~0.15 on the original setup) |
| `full_system` | `saved_map` ('') | Start from a saved map id: SLAM loads `map.{posegraph,data}` from `$ROBOT_WS/data/maps/<id>/`, else `robot_bringup/maps/<id>/`, and rag_node's memory session is pinned to the id. Empty: SLAM maps from scratch and the session is the fresh map's frame, `fresh_<world>_x<spawn x>_y<spawn y>` ([ADR-028](decisions/ADR-028-memory-session-per-map-frame.md)). An unknown id fails the launch |
| `full_system` | `saved_map_mode` (`localization`) | With `saved_map`: `localization` serves the saved `map.yaml` through map_server and localizes with AMCL, so nothing changes the map; `mapping` loads the pose graph into SLAM Toolbox instead and keeps adding scans. Any other value fails the launch ([ADR-035](decisions/ADR-035-saved-map-loads-read-only.md)) |
| `full_system` | `start_zone` ('') | Start the robot at this zone's centre: it is spawned there in the world, and SLAM (`map_start_pose`) or AMCL (`initial_pose`) is told where it is. Empty starts where the map begins. An unknown name fails the launch with the stored zones ([ADR-035](decisions/ADR-035-saved-map-loads-read-only.md)) |
| `demo` | `use_gz_gui` (true), `use_rviz` (true), `use_nav2` (true), `saved_map` (`house`), `saved_map_mode` (`localization`), `start_zone` (`entrada`) | `full_system` as the demo is recorded |
| `simulation` | `use_nav2`, `use_rviz`, `use_gz_gui`, `saved_map`, `saved_map_mode`, `start_zone`, `x_pose`, `y_pose` | The simulation half; `saved_map` affects SLAM and localization. `start_zone` overrides `x_pose`/`y_pose`. Fails at once if a Gazebo server is already running in its partition ([ADR-034](decisions/ADR-034-refuse-a-second-gazebo.md)) |
| `agent` | `use_sim_time` (true), `memory_session` ('') | The agent half; `memory_session` pins rag_node's session (full_system passes the saved map's id or the fresh frame's). Empty keeps the persisted session — for agent-only runs such as the offline benchmark |

`full_system` forwards `use_nav2`, `use_rviz`, `use_gz_gui`, `saved_map`, `saved_map_mode` and `start_zone` to
the simulation, and the resulting memory session to the agent. `simulation`
always starts `cmd_vel_mux_node`, with or without Nav2. A saved map must be loaded with the robot spawning where it was
mapped — the spawn defaults `x_pose:=-2.0 y_pose:=-0.5` in `simulation.launch.py` —
since SLAM starts at the map origin, where mapping began.

## ROS 2 parameters

Tuning defaults live in `robot_bringup/config/agent_params.yaml`.

| Node | Parameter | Meaning |
|---|---|---|
| llm_planner_node | `ollama_base_url` (`http://localhost:11434`) | Ollama server URL |
| llm_planner_node | `llm_model` (`qwen2.5:7b`) | Planner model name |
| llm_planner_node | `llm_temperature` (0.0) | Sampling temperature; 0 = repeatable plans (not identical across runs — rag-analysis, threats to validity) |
| llm_planner_node | `max_plan_steps` (10) | Plan steps executed at most, after `report` steps are stripped |
| llm_planner_node | `rag_score_threshold` (0.40) | Minimum cosine similarity for RAG context to enter the prompt (calibrated for bge-m3) |
| llm_planner_node | `rag_enabled` (true) | Ablation switch: disables all RAG retrieval |
| llm_planner_node | `zones_in_prompt` (true) | Ablation switch: withholds the known zones (names and centres) from the prompt |
| llm_planner_node | `dry_run` (false) | Produce/publish the plan but skip execution (benchmark mode) |
| llm_planner_node | `plan_validation` (true) | Check every `navigate` step before execution: an unknown zone, a point that is no known place, or a remembered place borrowed for one the goal asks for but memory lacks becomes `explore` ([ADR-032](decisions/ADR-032-plan-check-before-execution.md)). Costs one extra Qwen call per goal whose plan navigates to coordinates (~0.4 s median) |
| llm_planner_node | `memory_source` (`vector`) | How place memory is looked up when `rag_enabled` is true: `vector` (similarity search over `semantic_map`) or `sql` (Qwen writes a read-only SELECT over `places_db`, which holds the same places — the LLM → SQL experiment, [ADR-033](decisions/ADR-033-llm-to-sql-place-memory.md)). The knowledge base is retrieved the same way in both. Re-read per goal |
| llm_planner_node | `places_db` (`$ROBOT_WS/data/places.db`) | SQLite `places(name, x, y, zone, description)` for `memory_source: sql`, written by `eval/export_places_sql.py` |
| rag_node | `ollama_base_url` (`http://localhost:11434`) | Ollama server URL for embeddings |
| rag_node | `embedding_model` (`bge-m3`) | Ollama embedding model (see [rag-analysis](rag-analysis.md) §2.4) |
| rag_node | `collections` | Collections created on startup |
| rag_node | `top_k_default` (5) | Results returned when a request sets `top_k <= 0` |
| rag_node | `scene_merge_radius` (2.0) | Meters within which a re-observed place of the same look, zone and map session merges into its existing memory; `<= 0` disables ([ADR-025](decisions/ADR-025-scene-memory-merging.md)) |
| rag_node | `map_session_id` ('') | Pin the memory session ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)); `full_system` sets it to the saved map's id ([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)) or the fresh map's frame id ([ADR-028](decisions/ADR-028-memory-session-per-map-frame.md)); empty continues the persisted session |
| skills_executor_node | `explore_strategy` (`clusters`) | How `explore` picks its next frontier: `clusters` (most unexplored edge per metre, targets with clearance) or `nearest` (the original rule, for comparison); re-read every step ([ADR-027](decisions/ADR-027-exploration-frontier-clusters.md)) |
| cmd_vel_mux_node | `teleop_topic` (`/robot/cmd_vel_manual`) / `nav_topic` (`/cmd_vel_nav_out`) / `output_topic` (`/cmd_vel`) | Mux inputs and output ([ADR-029](decisions/ADR-029-cmd-vel-mux.md)) |
| cmd_vel_mux_node | `teleop_timeout_sec` (0.5) / `nav_timeout_sec` (0.5) | How long a source keeps control after its last command |
| dashboard_node | `http_host` (`127.0.0.1`) / `http_port` (8080) | HTTP bind address and port — loopback by default, the API is unauthenticated |
| dashboard_node | `cmd_vel_topic` (`/robot/cmd_vel_manual`) | Topic for manual driving commands — the mux's high-priority input |
| dashboard_node | `cmd_vel_stamped` (true) | Publish `geometry_msgs/TwistStamped` (this stack's Gazebo bridge and Nav2 both expect it); false for a plain-`Twist` base |
| dashboard_node | `teleop_linear_speed` (0.26) / `teleop_angular_speed` (1.82) | Manual driving speeds in m/s and rad/s — the Waffle's documented maxima, which `robot_dashboard.teleop` also clamps every command to (×1.4 with shift, capped there) |
| dashboard_node | `teleop_timeout_sec` (0.6) | Deadman window: a command not refreshed within it is replaced by a stop ([ADR-023](decisions/ADR-023-browser-teleop.md)) |
| dashboard_node | `teleop_rate_hz` (20.0) | Rate at which a held command is republished |

The teleop speeds are re-read on every command and `explore_strategy` on every
exploration step, so `ros2 param set` changes them mid-session. `rag_enabled`, `zones_in_prompt`, `dry_run`, `plan_validation` and `memory_source` are re-read on every goal, so the
benchmark can flip conditions with `ros2 param set` without restarting the node
(a restart would reset the shared SLAM map and break cross-condition fairness).

### Filesystem paths

Path parameters are deliberately **absent** from `agent_params.yaml`. Each node
derives its default from the `ROBOT_WS` environment variable (exported by
`setup_env.sh`, which resolves its own directory), falling back to
`~/robot_ws`. This keeps the workspace clone-location independent.

| Node | Parameter | Default |
|---|---|---|
| llm_planner_node, skills_executor_node, dashboard_node | `zones_db` | `$ROBOT_WS/data/zones.db` |
| rag_node, skills_executor_node | `logs_dir` | `$ROBOT_WS/data/logs` |
| rag_node, skills_executor_node | `maps_dir` | `$ROBOT_WS/data/maps` (map session + saved maps, [ADR-019](decisions/ADR-019-map-session-memory-versioning.md)) |
| rag_node | `chroma_db_path` | `$ROBOT_WS/data/chroma_db` |
| rag_node | `knowledge_dir` | `$ROBOT_WS/data/knowledge` |

Override an individual path the usual way:

```bash
ros2 run robot_dashboard dashboard_node --ros-args -p zones_db:=/tmp/zones.db
```

## Maintenance tools

### `ros2 run robot_rag compact_memory`

Maintenance of `semantic_map` with the stack stopped
([ADR-025](decisions/ADR-025-scene-memory-merging.md)). A dry run by default;
`--apply` refuses while `rag_node` runs and copies `data/chroma_db` to
`data/chroma_db.bak-<timestamp>` first.

| Option | Effect |
|---|---|
| (none) | Fold duplicate scene memories stored before merge-on-write: same look, zone and map session within `--radius` (2.0 m). Idempotent |
| `--prune-untagged` | Also delete memories with no map session, which no session can retrieve. Other sessions' memories are never touched |
| `--retag-session OLD NEW` | Move memories from session `OLD` to `NEW` first — only when both name the same coordinate frame ([ADR-028](decisions/ADR-028-memory-session-per-map-frame.md)) |
| `--chroma-db-path PATH` | Store to operate on (default `$ROBOT_WS/data/chroma_db`) |
