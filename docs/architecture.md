# Architecture — Robot RAG Agent

## Overview

Seven ROS 2 packages cooperate to take a natural-language instruction all the
way to physical robot actions in simulation:

```
robot_interfaces  → shared msgs/srvs (build first)
robot_zones       → shared SQLite store of named zones
robot_rag         → semantic memory (ChromaDB) + RAG
robot_skills      → physical/perception skill execution
robot_brain       → LLM planning + orchestration
robot_dashboard   → web dashboard: observability + goals (text/voice)
robot_bringup     → launch files and system integration
```

## Nodes and responsibilities

### `rag_node` (robot_rag)

Exposes `/rag/query` and `/rag/update_map`. Maintains three ChromaDB
collections (`semantic_map`, `knowledge_base`, `task_history`), each with
`hnsw:space: cosine`. On startup it ingests `data/knowledge/*.md` into
`knowledge_base` (Markdown-section chunking — headers grouped with their
body, not split) and `data/logs/*.json` into `task_history`.

Objects stored in `semantic_map` embed their **map-frame coordinates in the
document text** ("`refrigerator at (x=3.42, y=-1.15) in kitchen: ...`") —
`QueryRAG` returns only document text, so this is the channel through which
the planner learns *where* a remembered thing is
([ADR-012](decisions/ADR-012-navigable-rag-post-execution-report.md)).

Embeddings via `bge-m3` (Ollama, 1024 dims; multilingual — chosen over
nomic-embed-text on measured retrieval data, see docs/rag-analysis.md §2.4). See
[ADR-001](decisions/ADR-001-chromadb.md) and
[ADR-011](decisions/ADR-011-rag-quality-zones-sqlite.md) (chunking, real
task_history, relevance threshold).

### `robot_zones` (shared package, no node)

`ZoneStore` — SQLite CRUD (`data/zones.db`) for named rectangular zones
created on the dashboard. Written by `dashboard_node`; read by
`skills_executor_node` (zone resolution for `navigate`/`explore`) and
`llm_planner_node` (known-zones list in the prompt). See
[ADR-011](decisions/ADR-011-rag-quality-zones-sqlite.md) (supersedes
[ADR-008](decisions/ADR-008-user-zones.md), which used a flat JSON file).

### `skills_executor_node` (robot_skills)

Exposes `/skills/execute`, dispatching by `skill_name` to five
implementations:

- **navigate** (`nav_skill.py`): Nav2 `BasicNavigator` (SimpleCommander API).
  Takes explicit `(x, y, theta)`; named zones are resolved to coordinates
  upstream against the SQLite store (no hardcoded fallbacks).
- **explore** (`explore_skill.py`): basic frontier exploration — nearest free
  cell adjacent to unknown space in the `/map` grid, skipping frontiers near
  the robot and previously attempted ones; optional zone bounds restrict the
  search area.
- **perceive** (`perceive_skill.py` + `scene_descriptor.py`): classical
  scene description — dominant camera colors + LIDAR clutter metrics, no ML
  ([ADR-014](decisions/ADR-014-classical-scene-descriptor.md)) — stored in
  `semantic_map` with the robot's coordinates via `/rag/update_map`.
  **explore** stores one description at every frontier it reaches, so the
  semantic memory builds itself during exploration.
- **scan_360** (`nav_skill.py` spin + `scene_descriptor.describe_scan_360`):
  an in-place full turn (default 8 steps of 45°) for "look all around you"
  goals. A LIDAR scan already covers 360° in one reading, so only the
  camera's narrow FOV needs the rotation — colors sampled at each heading are
  merged (a color must appear in ≥1/4 of headings to count) into one
  panoramic description, stored the same way as **perceive**.
- **report** (`report_skill.py`): publishes `/robot/response` and writes a
  JSON log to `data/logs/<task_id>.json` (later ingested into the
  `task_history` RAG collection).

Robot pose comes from TF (`map -> base_link`), not AMCL — see
[ADR-004](decisions/ADR-004-slam-toolbox-no-amcl.md).

### `llm_planner_node` (robot_brain)

Subscribes to `/robot/goal`. For each goal:

1. Retrieves context from `/rag/query` over `knowledge_base`, `semantic_map`,
   and `task_history`, dropping hits below `rag_score_threshold` (an
   irrelevant hit is worse than none — it enters the prompt as ground truth).
2. Builds the prompt (`prompts.py`) and requests a JSON plan from Qwen2.5-7B
   (`qwen_client.py`, `temperature=0` for reproducibility).
3. Publishes the raw plan on `/robot/plan` and executes the steps
   sequentially via `/skills/execute` (`toolkit.py` — plain dispatch, no
   agent framework; see ADR-012 for why LangChain was removed).
4. After execution, a **second LLM call** receives the real step results and
   writes the final user-facing response (in the user's language), published
   via the report skill. The planner never pre-writes outcomes.

Ablation switches for the evaluation (`rag_enabled`, `zones_in_prompt`,
`dry_run`) are re-read on every goal, so the benchmark can flip conditions
live with `ros2 param set`.

### `dashboard_node` (robot_dashboard)

Serves a web dashboard at `http://localhost:8080` (FastAPI + uvicorn on a
thread inside the node). Live planning timeline (`/robot/goal`,
`/robot/status`, `/robot/response`), filterable `/rosout` viewer, and an
interactive SLAM map: robot pose, named zones drawn as overlays, and
drag-to-select area creation (saved to SQLite and indexed into semantic
memory). Goals can be typed or spoken (browser Web Speech API). See
[ADR-005](decisions/ADR-005-dashboard-fastapi.md).

## Data flow

```
/robot/goal (String)
    │
    ▼
llm_planner_node ──/rag/query──► rag_node ──► ChromaDB
    │
    ▼ (prompt + filtered context + known zones)
Qwen2.5-7B (Ollama) → plan JSON {reasoning, steps[]}  ──► /robot/plan
    │
    ▼ execute_plan()
/skills/execute ──► skills_executor_node
    │                     │
    ├─ navigate ─► Nav2   ├─ perceive ──► scene descriptor ─► /rag/update_map
    ├─ explore ──► Nav2 + frontier search  (each frontier also perceives+stores)
    └─ scan_360 ─► Nav2 spin (in place) ─► scene descriptor ─► /rag/update_map
    │
    ▼ (real step results)
Qwen2.5-7B (2nd call) → grounded response ──► report ──► /robot/response
                                                    └──► data/logs/*.json
```

## Concurrency model

Nodes that make service calls from inside callbacks (`llm_planner_node`,
`skills_executor_node`) run on a `MultiThreadedExecutor` with the critical
clients/subscriptions in separate callback groups, and wait on futures with
`threading.Event` — never with nested spins or throwaway executors. See
[ADR-007](decisions/ADR-007-executors-callback-groups.md) for the two failed
patterns that motivated this.

## Evaluation (eval/)

The `eval/` harness quantifies whether RAG improves navigation, at the
planning level ([ADR-013](decisions/ADR-013-planning-level-benchmark.md)):
`seed_memory.py` registers landmarks in semantic memory, `run_benchmark.py`
runs task suites across ablation conditions in the planner's `dry_run` mode
scoring each plan from `/robot/plan`, and `report.py` aggregates results into
Markdown tables. Results live in `eval/results/`.

## Execution environment (WSL2 + venv)

ROS 2 nodes build and run with the system Python (the one that ships
`rclpy`). Agent dependencies (ChromaDB, Ollama client, FastAPI) live in the
`agent_env` venv. The two are bridged via `PYTHONPATH` in `setup_env.sh` —
never by activating the venv — see
[ADR-003](decisions/ADR-003-venv-pythonpath-bridge.md). Every new terminal
must `source ~/robot_ws/setup_env.sh` before building or launching.

## Known limitations

- **DDS on WSL2:** CycloneDDS is pinned to loopback via `cyclonedds.xml` +
  `CYCLONEDDS_URI` ([ADR-006](decisions/ADR-006-cyclonedds-loopback.md));
  without it, local pub/sub discovery was intermittent across the multiple
  NICs (`eth0`, `docker0`).
- **Goals outside the SLAM map:** Nav2 rejects goals beyond the currently
  mapped bounds ("outside bounds"); unexplored areas must be mapped first.
  The planner's rules instruct it to explore when a location is unknown.
- **High-resolution camera = silently dropped messages.** The stock
  TurtleBot3 model publishes 1920×1080 (~55 MB/s); BEST_EFFORT subscribers in
  a busy process lost every frame at the DDS layer with no visible error. Our
  model copy uses 640×480 — see
  [ADR-009](decisions/ADR-009-camera-resolution-bridge.md).
- **End-to-end navigation is unreliable on this software-rendered sim** (the
  robot wedges in narrow doorways; `navigate` can report false success),
  which is why the benchmark measures the planning decision — see
  [ADR-013](decisions/ADR-013-planning-level-benchmark.md).
