# API Reference

## Mensajes

### `SemanticObject.msg`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| object_id | string | Identificador único del objeto detectado |
| label | string | Clase del objeto, ej. "chair", "door", "bottle" |
| confidence | float32 | Confianza de la detección [0.0, 1.0] |
| pose | geometry_msgs/Pose | Pose 2D/3D del objeto en el frame del mapa |
| description | string | Descripción libre generada por Qwen-VL |
| room_zone | string | Zona semántica, ej. "kitchen", "corridor" |
| timestamp | builtin_interfaces/Time | Última vez observado |

### `TaskPlan.msg`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| goal_text | string | Goal original en lenguaje natural |
| steps | string[] | Pasos del plan serializados como JSON |
| status | string | "pending" \| "running" \| "done" \| "failed" |

## Servicios

### `/rag/query` (QueryRAG.srv)

Recupera contexto relevante de ChromaDB dado un texto de consulta.

**Request:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| query_text | string | Consulta en lenguaje natural |
| collection_name | string | "semantic_map" \| "knowledge_base" \| "task_history" |
| top_k | int32 | Número de resultados (default: 5) |

**Response:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| contexts | string[] | Fragmentos recuperados por similitud |
| scores | float32[] | Puntuaciones coseno [0.0, 1.0] |
| success | bool | False si falla la consulta o la colección no existe |
| error_msg | string | Vacío si success=True |

Servida por `rag_node`.

### `/rag/update_map` (UpdateMap.srv)

Inserta o actualiza un objeto detectado en la colección `semantic_map`.

**Request:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| object_data | robot_interfaces/SemanticObject | Objeto a insertar/actualizar |

**Response:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| success | bool | False si la actualización falló |
| error_msg | string | Vacío si success=True |

Servida por `rag_node`. Llamada por `skills_executor_node` tras cada
`perceive`.

### `/skills/execute` (ExecuteSkill.srv)

Despacha un skill por nombre con parámetros en JSON.

**Request:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| skill_name | string | "navigate" \| "explore" \| "perceive" \| "report" |
| params_json | string | Parámetros codificados en JSON |

**Response:**
| Campo | Tipo | Descripción |
|-------|------|-------------|
| result_json | string | Resultado codificado en JSON |
| success | bool | False si el skill falló |
| error_msg | string | Vacío si success=True |

Servida por `skills_executor_node`.

**Parámetros por skill:**

| skill_name | params_json | result_json |
|---|---|---|
| navigate | `{"zone": "kitchen"}` o `{"x": 1.0, "y": 0.5, "theta": 0.0}` | `{"reached": bool, "message": str}` |
| explore | `{"duration_sec": 30}` | `{"visited_frontiers": int, "message": str}` |
| perceive | `{"query": "list all objects", "zone": "kitchen"}` | `{"objects": [{"label", "confidence", "description", "object_id"}]}` |
| report | `{"message": "...", "goal_text": "..."}` | `{"published": bool}` |

## Topics

| Topic | Tipo | Publicado por | Consumido por |
|---|---|---|---|
| /robot/goal | std_msgs/String | usuario (CLI/UI) | llm_planner_node |
| /robot/status | std_msgs/String | llm_planner_node | usuario |
| /robot/response | std_msgs/String | skills_executor_node (report_skill) | usuario |
| /semantic_objects | robot_interfaces/SemanticObject | (extensible: otros detectores) | rag_node |
| /camera/image_raw | sensor_msgs/Image | Gazebo/TurtleBot3 | skills_executor_node |
| /map | nav_msgs/OccupancyGrid | slam_toolbox | skills_executor_node |

## Dashboard HTTP (robot_dashboard)

Servido por `dashboard_node` en `http://localhost:8080` (parámetros
`http_host`/`http_port`).

### `GET /`

Página única del dashboard (timeline de planificación, logs, mapa, envío de
goals por texto/voz).

### `GET /api/events?since=<id>`

Devuelve los eventos con id mayor que `since` y la metadata del mapa SLAM.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| events | object[] | `{id, ts, type, text, source, level}`; type ∈ goal\|status\|response\|rosout |
| map | object\|null | `{width_m, height_m, x_min, x_max, y_min, y_max}` |

### `POST /api/goal`

Publica un goal en `/robot/goal`.

**Body:** `{"text": "Ve a la cocina..."}` → `{"ok": true}` (400 si vacío).

### `GET /api/map`

Mapa SLAM renderizado + pose del robot + zonas.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| map | object\|null | `{png_b64, resolution, origin_x, origin_y, width, height}` (PNG con fila superior = y_max) |
| robot | object\|null | `{x, y}` en el frame del mapa (TF map→base_link) |
| zones | object | `{nombre: {x_min, y_min, x_max, y_max}}` |

### `POST /api/zones` / `DELETE /api/zones/{name}`

Crea (y indexa en memoria semántica) o borra una zona nombrada. Ver
[ADR-008](decisions/ADR-008-zonas-usuario.md).

**Body POST:** `{"name": "cocina", "x_min": ..., "y_min": ..., "x_max": ..., "y_max": ...}`

## Parámetros ROS 2

Ver `robot_bringup/config/agent_params.yaml` para los valores por defecto de
`rag_node`, `skills_executor_node`, `llm_planner_node` y `dashboard_node`.
Destacados: `rag_score_threshold` (llm_planner_node, default 0.45 — descarta
contexto RAG por debajo de este score de similitud coseno antes de meterlo
al prompt) y `zones_db` (skills_executor_node/llm_planner_node/
dashboard_node — ruta compartida a `data/zones.db`, ver
[ADR-011](decisions/ADR-011-rag-mejoras-y-zonas-sqlite.md)).
