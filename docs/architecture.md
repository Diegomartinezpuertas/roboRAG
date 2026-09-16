# Architecture — Robot RAG Agent

## Overview

Seven ROS 2 packages cooperate to take a natural-language instruction all the
way to physical robot actions in simulation:

```
robot_interfaces  → shared msgs/srvs (build first)
robot_zones       → shared SQLite store of named zones + what each room is for
robot_rag         → semantic memory (ChromaDB) + RAG
robot_skills      → physical/perception skill execution
robot_brain       → LLM planning + orchestration
robot_dashboard   → web dashboard: observability, goals, memory viewer, manual driving
robot_bringup     → launch files and system integration
```

## Nodes and responsibilities

### `rag_node` (robot_rag)

Exposes `/rag/query`, `/rag/inspect`, `/rag/delete` and `/rag/update_map`. Maintains three ChromaDB
collections (`semantic_map`, `knowledge_base`, `task_history`), each with
`hnsw:space: cosine`. On startup it syncs `data/knowledge/*.md` into
`knowledge_base` (Markdown-section chunking — headers grouped with their
body, not split; re-embedded only when the files changed, ADR-031) and ingests
`data/logs/*.json` into `task_history`.

Objects stored in `semantic_map` embed their **map-frame coordinates in the
document text** ("`refrigerator at (x=3.42, y=-1.15) in kitchen: ...`") —
`QueryRAG` returns only document text, so this is the channel through which
the planner learns *where* a remembered thing is
([ADR-012](decisions/ADR-012-navigable-rag-post-execution-report.md)).

`/rag/inspect` is the read-only counterpart of `/rag/query`, for humans rather
than the planner: it browses a collection as stored (no embedding call) or
searches it, and returns ids, metadata and scores so the dashboard can render
memories as structured cards instead of prose
([ADR-024](decisions/ADR-024-memory-inspection-service.md)).

Coordinate memories are scoped to a **map session**
([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)): writes are
tagged with it and retrieval of `semantic_map`/`task_history` is filtered to it.
The launch pins the session — a saved map's id, or for a map built from scratch
the id of its frame, `fresh_<world>_x<spawn x>_y<spawn y>`, so fresh maps of the
same frame share memories and another world or spawn pose never sees them
([ADR-028](decisions/ADR-028-memory-session-per-map-frame.md)).

Scene observations are **merged on write**: a re-observed place of the same
look (colour set + clutter class), zone and map session within 2 m updates the
existing memory instead of adding another, and `compact_memory` applies the same
rule to data stored before it existed
([ADR-025](decisions/ADR-025-scene-memory-merging.md)).

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

`room_semantics.py` — the other half of what a zone name means. A table of room
types (Spanish and English aliases, plus one sentence per language on what
happens in that room) turns "cocina" into text a functional query can match, so
"ve donde se suele cocinar" retrieves the kitchen. Used by `dashboard_node` when
indexing a zone and by `skills_executor_node` when storing a scene observed
inside one; a name it does not recognize is left plain
([ADR-022](decisions/ADR-022-room-semantics.md)).

### `skills_executor_node` (robot_skills)

Exposes `/skills/execute`, dispatching by `skill_name` to five
implementations:

- **navigate** (`nav_skill.py`): Nav2 `BasicNavigator` (SimpleCommander API).
  Takes explicit `(x, y, theta)`; named zones are resolved to coordinates
  upstream against the SQLite store (no hardcoded fallbacks).
- **explore** (`explore_skill.py`): frontier exploration. Frontier cells (free
  cells touching unknown space in `/map`) are grouped into clusters; the robot
  heads for the cluster with the most unexplored edge per metre of travel,
  aiming at the cluster's best eligible cell — most clearance from walls, and
  not beside the robot or already attempted. Measured: ~40–55% more area than
  the original nearest-cell rule in the same time
  ([ADR-027](decisions/ADR-027-exploration-frontier-clusters.md)). Optional zone
  bounds restrict the search.
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

### `cmd_vel_mux_node` (robot_skills)

The only publisher the robot base listens to. Manual driving
(`/robot/cmd_vel_manual`, priority 2) and Nav2 (`/cmd_vel_nav_out`, the
collision monitor's output, priority 1) each publish to their own topic; the
highest-priority source that spoke within 0.5 s reaches `/cmd_vel`, and the
active one is published on `/robot/cmd_vel_source`. A key pressed mid-goal takes
over; letting go hands control back — verified live
([ADR-029](decisions/ADR-029-cmd-vel-mux.md)). Launched by `simulation.launch.py`
with or without Nav2. Nav2's `docking_server` still publishes `/cmd_vel` directly,
only while docking, which this project never requests.

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
thread inside the node), laid out as a live floor plan
([ADR-030](decisions/ADR-030-dashboard-redesign-and-browser-tests.md)): the SLAM
map drawn as a blueprint fills the left of the screen — robot with heading and
trail, rooms hatched, memories as markers, drag-to-select to name a room (saved
to SQLite and indexed into semantic memory) — with the drive controls and a
title block (connection, simulation speed, map size, pose) framing it. The right
column holds the order box (typed or spoken, browser Web Speech API), the
agent's reasoning as a thread (order, reasoning, steps, answer; `/rosout` one
tab away) and the memory. See [ADR-005](decisions/ADR-005-dashboard-fastapi.md).

At start, once `/rag/update_map` answers, every stored zone is indexed again
into the active memory session, so zones survive a change of session
([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)). Map images are
versioned per server process, never by the grid stamp alone — simulation time
restarts every launch.

Two panels make the system legible and drivable from the same window:

- **Memory viewer.** Cards per memory — name, what kind of memory it is,
  similarity bar, coordinates, how many observations it stands for — over
  `/rag/inspect`, with each coordinate memory drawn on the map where it was
  learned, memories from another map session greyed out (ADR-019 made visible),
  and a delete button for `semantic_map` memories (`/rag/delete`). Browsing costs
  no embedding call; searching costs one
  ([ADR-024](decisions/ADR-024-memory-inspection-service.md)).
- **Manual driving.** WASD publishes to `/robot/cmd_vel_manual`, which the mux
  puts ahead of Nav2 (ADR-029), so a person can map the house by hand, take over
  mid-goal, name the room the robot is standing in, and save the map. The
  browser sends *keys*, the node owns the speeds, and a deadman stops the robot
  if the refreshes stop arriving ([ADR-023](decisions/ADR-023-browser-teleop.md)).
  The panel says who holds `/cmd_vel`, whether the keyboard reaches the page, and
  the simulation's real-time factor (estimated from `/clock`) — the three reasons
  a robot can look like it is not moving.

The HTTP layer (`web_api.py`) holds no `rclpy` import and is built against a
node *interface*, so every endpoint is exercised in the pure-logic suite; the
velocity and deadman logic lives in `teleop.py`, and the real-time-factor
estimate in `sim_clock.py`, for the same reason
([ADR-018](decisions/ADR-018-test-strategy.md)). The page itself is tested in
headless Chromium (`tests/test_dashboard_ui.py`): a held W reaches
`/api/teleop`, typing an order never drives, cards delete what they stand for.

## Data flow

```
/robot/goal (String)                      dashboard_node ──/rag/inspect, /rag/delete──► rag_node
    │                                         │  (memory viewer)
    │                                         └──/robot/cmd_vel_manual──┐
    │                                                                   ▼
    │                                Nav2 ──/cmd_vel_nav_out──► cmd_vel_mux_node ──/cmd_vel──► robot
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
    ├─ explore ──► Nav2 + frontier clusters  (each frontier also perceives+stores)
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

Shutdown is the other half of that discipline: every `main()` catches
`ExternalShutdownException` alongside `KeyboardInterrupt` and guards
`rclpy.shutdown()` with `rclpy.ok()`, so stopping a launch exits silently
instead of printing a traceback per node
([ADR-016](decisions/ADR-016-node-shutdown-contract.md)).

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
must `source setup_env.sh` before building or launching.

That script also resolves its own directory and exports it as `ROBOT_WS`, which
is what every node uses to build its default data paths — no absolute path is
hardcoded anywhere, so the workspace works from any clone location
([ADR-015](decisions/ADR-015-workspace-relative-paths.md)).

### The container

A second, narrower environment covers everything that does not need rendering
or a GPU: the workspace build, `ruff`, and both test layers
([ADR-021](decisions/ADR-021-container-reproducibility.md)).

```bash
docker build -t robot-rag-agent . && docker run --rm robot-rag-agent
```

Two details make it a verification tool rather than a convenience. It sources
this same `setup_env.sh`, so the container has no private copy of the
environment to drift from; and it runs at `/robot_ws`, so every execution is a
standing check on the ADR-015 path portability that the review found broken.

There is no venv in it. ADR-003's bridge exists because activating a venv
swaps the `python3` that `colcon` and the generated console-script shebangs
expect — in a container the system Python is the project Python, so the problem
it solves does not exist.

The simulator stays outside. Gazebo already renders on `llvmpipe` here, and
containerising it would advertise the one capability the documentation is
careful to call unreliable.

## Known limitations

- **DDS on WSL2:** CycloneDDS is pinned to loopback via `cyclonedds.xml` +
  `CYCLONEDDS_URI` ([ADR-006](decisions/ADR-006-cyclonedds-loopback.md));
  without it, local pub/sub discovery was intermittent across the multiple
  NICs (`eth0`, `docker0`).
- **Goals outside the SLAM map:** Nav2 rejects goals beyond the currently
  mapped bounds ("outside bounds"); unexplored areas must be mapped first.
  The planner's rules instruct it to explore when a location is unknown. A
  house mapped once — by hand with WASD, saved from the dashboard — can be
  loaded with `saved_map:=<id>` so a run starts with the whole map
  ([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)).
- **Goals against walls:** a navigation goal placed inside a wall's inflated
  cost (`inflation_radius: 0.5`) can fail to plan even where the robot drives
  past freely — measured: it crosses a 0.7 m doorway both ways at 0.5 m. The
  explorer used to place frontier goals exactly there; it now picks the
  frontier cell with the most clearance
  ([ADR-027](decisions/ADR-027-exploration-frontier-clusters.md)).
- **High-resolution camera = silently dropped messages.** The stock
  TurtleBot3 model publishes 1920×1080 (~55 MB/s); BEST_EFFORT subscribers in
  a busy process lost every frame at the DDS layer with no visible error. Our
  model copy uses 640×480 — see
  [ADR-009](decisions/ADR-009-camera-resolution-bridge.md).
- **End-to-end navigation is unreliable on this software-rendered sim** (the
  robot wedges in narrow doorways; `navigate` can report false success),
  which is why the benchmark measures the planning decision — see
  [ADR-013](decisions/ADR-013-planning-level-benchmark.md).
