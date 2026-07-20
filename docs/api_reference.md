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
| perceive | `{"zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "stored": bool}` |
| scan_360 | `{"steps": 8 (optional, 4-16), "zone": "kitchen" (optional)}` | `{"colors": [...], "clutter": str, "obstacle_clusters": int, "description": str, "headings_completed": int, "stored": bool}` |
| report | `{"message": "...", "goal_text": "..."}` | `{"published": bool}` |
| save_map † | `{"name": "home" (optional)}` | `{"map_id": str, "path": str, "message": str}` |

Zone names are resolved against the SQLite zone store; unknown zones fail
with an explicit error (no hardcoded fallbacks).

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

## Dashboard HTTP (robot_dashboard)

Served by `dashboard_node` at `http://localhost:8080` (`http_host`/`http_port`
parameters).

### `GET /`

Single-page dashboard (planning timeline, logs, interactive map, text/voice
goal input).

### `GET /api/events?since=<id>`

Events with id greater than `since`.

| Field | Type | Description |
|-------|------|-------------|
| events | object[] | `{id, ts, type, text, source, level}`; type ∈ goal\|status\|response\|rosout |

### `GET /api/map`

Rendered SLAM map + robot pose + zones.

| Field | Type | Description |
|-------|------|-------------|
| map | object\|null | `{png_b64, resolution, origin_x, origin_y, width, height}` (PNG top row = y_max) |
| robot | object\|null | `{x, y}` in the map frame (TF map→base_link) |
| zones | object | `{name: {x_min, y_min, x_max, y_max}}` |

### `POST /api/goal`

Publishes a goal on `/robot/goal`.

**Body:** `{"text": "Go to the kitchen..."}` → `{"ok": true}` (400 if empty).

### `POST /api/zones` / `DELETE /api/zones/{name}`

Creates (and indexes into semantic memory) or deletes a named zone. See
[ADR-011](decisions/ADR-011-rag-quality-zones-sqlite.md).

**POST body:** `{"name": "kitchen", "x_min": ..., "y_min": ..., "x_max": ..., "y_max": ...}`

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
| dashboard_node | `http_host` (`0.0.0.0`) / `http_port` (8080) | HTTP bind address and port |

`rag_enabled`, `zones_in_prompt` and `dry_run` are re-read on every goal, so the
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
