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

Served by `rag_node`. Called by `skills_executor_node` after each `perceive`
and by `dashboard_node` when a zone is created.

### `/skills/execute` (ExecuteSkill.srv)

Dispatches a skill by name with JSON-encoded parameters.

**Request:**
| Field | Type | Description |
|-------|------|-------------|
| skill_name | string | "navigate" \| "explore" \| "perceive" \| "scan_360" \| "report" |
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
| perceive | `{"zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "zone": str, "object_id": str, "stored": bool}` |
| scan_360 | `{"steps": 8 (optional, 4-16), "zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "zone": str, "object_id": str, "headings_completed": int, "stored": bool}` |
| report | `{"message": "...", "goal_text": "..."}` | `{"published": bool}` |
| save_map † | `{"name": "home" (optional)}` | `{"map_id": str, "path": str, "message": str}` |

Zone names are resolved against the SQLite zone store; unknown zones fail
with an explicit error (no hardcoded fallbacks). `zone` in a perceive/scan_360
result is the zone the observation was made in (empty outside every zone); when
that zone's name denotes a kind of room, what the room is for is appended to the
stored description ([ADR-022](decisions/ADR-022-room-semantics.md)).

† `save_map` is a **maintenance** skill: it serializes the live SLAM map (via
SLAM Toolbox) under the active map-session id, for persistence and memory
versioning ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)). It
is deliberately absent from the planner's prompt and from `toolkit.VALID_SKILLS`,
so the LLM never emits it — reach it only with a direct `ros2 service call`.

## Topics

| Topic | Type | Published by | Consumed by |
|---|---|---|---|
| /robot/goal | std_msgs/String | user (CLI / dashboard) | llm_planner_node |
| /robot/status | std_msgs/String | llm_planner_node | user / dashboard |
| /robot/plan | std_msgs/String (plan JSON) | llm_planner_node | eval harness / dashboard |
| /robot/response | std_msgs/String | skills_executor_node (report) | user / dashboard |
| /camera/image_raw | sensor_msgs/Image | Gazebo bridge | skills_executor_node |
| /scan | sensor_msgs/LaserScan | Gazebo bridge | skills_executor_node (clutter metrics) |
| /map | nav_msgs/OccupancyGrid | slam_toolbox | skills_executor_node, dashboard_node |
| /cmd_vel | geometry_msgs/TwistStamped | Nav2 (autonomous) / dashboard_node (manual driving, [ADR-023](decisions/ADR-023-browser-teleop.md)) | Gazebo bridge |

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

Events with id greater than `since`.

| Field | Type | Description |
|-------|------|-------------|
| events | object[] | `{id, ts, type, text, source, level}`; type ∈ goal\|status\|response\|rosout |

### `GET /api/map`

Map geometry + robot pose + zones. Light enough to poll every 2 s for the
pose; the image itself comes from `/api/map/png`.

| Field | Type | Description |
|-------|------|-------------|
| map | object\|null | `{stamp, resolution, origin_x, origin_y, width, height}` — `stamp` is the grid's timestamp (ns), the version key for `/api/map/png` |
| robot | object\|null | `{x, y}` in the map frame (TF map→base_link) |
| zones | object | `{name: {x_min, y_min, x_max, y_max}}` |

### `GET /api/map/png`

The rendered SLAM map as `image/png` (top row = y_max). Versioned with an
`ETag` equal to the grid's `stamp` and `Cache-Control: no-cache`, so a
client that sends `If-None-Match` gets `304 Not Modified` while SLAM has not
published a newer grid — the dashboard used to resend tens of KB of
unchanged base64 on every poll. `404` before the first map arrives.

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
| entries | object[] | `{id, title, document, score, label, zone, source, x, y, map_id, stale}` |
| error | string | Empty when ok |

`score` is `-1.0` while browsing, `x`/`y` are `null` for entries without a pose,
and `stale` marks a coordinate memory written against a different map. **Always
`200`**, including when `rag_node` is down: the dashboard starts before it, and
the panel shows the reason rather than failing the request.

### `POST /api/teleop` / `POST /api/teleop/stop`

Manual driving ([ADR-023](decisions/ADR-023-browser-teleop.md)). The browser
sends the keys it is holding, never a velocity; the node applies its own speed
parameters and restarts its deadman.

**Body:** `{"keys": ["w", "a"], "boost": false}`
→ `{"ok": true, "linear": 0.18, "angular": 1.0, "driving": true}`

Held keys must be re-sent (the UI does so every 150 ms). A command not refreshed
within `teleop_timeout_sec` expires and the robot is stopped. `/api/teleop/stop`
ends the session immediately; an empty `keys` list means the same thing.

### `POST /api/map/save`

Persists the live SLAM map through the `save_map` maintenance skill
([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)) — what makes a
manual mapping run worth doing.

**Body:** `{"name": "casa"}` (normalized like a zone name; empty saves under the
active session id) → `{"ok": true, "result": {"map_id": ..., "path": ...}}`, or
`503` with the reason when the agent stack is not running.

## ROS 2 parameters

Tuning defaults live in `robot_bringup/config/agent_params.yaml`.

| Node | Parameter | Meaning |
|---|---|---|
| llm_planner_node | `ollama_base_url` (`http://localhost:11434`) | Ollama server URL |
| llm_planner_node | `llm_model` (`qwen2.5:7b`) | Planner model name |
| llm_planner_node | `llm_temperature` (0.0) | Sampling temperature; 0 = deterministic plans |
| llm_planner_node | `max_plan_steps` (10) | Plan steps executed at most, after `report` steps are stripped |
| llm_planner_node | `rag_score_threshold` (0.40) | Minimum cosine similarity for RAG context to enter the prompt (calibrated for bge-m3) |
| llm_planner_node | `rag_enabled` (true) | Ablation switch: disables all RAG retrieval |
| llm_planner_node | `zones_in_prompt` (true) | Ablation switch: withholds known-zone names |
| llm_planner_node | `dry_run` (false) | Produce/publish the plan but skip execution (benchmark mode) |
| rag_node | `embedding_model` (`bge-m3`) | Ollama embedding model (see [rag-analysis](rag-analysis.md) §2.4) |
| rag_node | `collections` | Collections created on startup |
| rag_node | `top_k_default` (5) | Results returned when a request sets `top_k <= 0` |
| rag_node | `map_session_id` ('') | Pin the memory session to a saved map's id on reload ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)); empty continues the current session |
| dashboard_node | `http_host` (`127.0.0.1`) / `http_port` (8080) | HTTP bind address and port — loopback by default, the API is unauthenticated |
| dashboard_node | `cmd_vel_topic` (`/cmd_vel`) | Topic for manual driving commands |
| dashboard_node | `cmd_vel_stamped` (true) | Publish `geometry_msgs/TwistStamped` (this stack's Gazebo bridge and Nav2 both expect it); false for a plain-`Twist` base |
| dashboard_node | `teleop_linear_speed` (0.18) / `teleop_angular_speed` (1.0) | Manual driving speeds in m/s and rad/s; ×1.4 with shift, still inside the Waffle's 0.26 / 1.82 maxima |
| dashboard_node | `teleop_timeout_sec` (0.6) | Deadman window: a command not refreshed within it is replaced by a stop ([ADR-023](decisions/ADR-023-browser-teleop.md)) |
| dashboard_node | `teleop_rate_hz` (20.0) | Rate at which a held command is republished |

The teleop speeds are re-read on every command, so `ros2 param set` retunes
driving mid-session. `rag_enabled`, `zones_in_prompt` and `dry_run` are re-read on every goal, so the
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
