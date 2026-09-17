# CLAUDE.md — Robot RAG Agent

> Context guide for Claude Code. Read it fully before touching any file.

---

## What this project is

A cognitive robotic agent running in simulation (ROS 2 Jazzy + Gazebo
Harmonic). It receives natural-language instructions, consults a semantic
memory (RAG with ChromaDB), plans with a local LLM (Qwen2.5-7B via Ollama),
and executes actions on the robot (TurtleBot3 Waffle) through ROS 2 skills.

**Purpose:** personal robotics-engineering portfolio project. Documentation
is as important as code — every decision must be recorded (ADRs).

---

## Hardware and environment

```
OS:        Windows 11 + WSL2 Ubuntu 24.04
CPU:       Intel Core Ultra 7 HX
GPU:       NVIDIA RTX 5070 8GB (reachable from WSL2 via Ollama/CUDA)
NPU:       Intel NPU — NOT reachable from WSL2 (reserved for native Windows)
RAM:       32GB
Shell:     bash on WSL2
Python:    ~/robot_ws/agent_env (venv — bridged, never activated for ROS nodes)
ROS2:      Jazzy Jalisco
Simulator: Gazebo Harmonic (llvmpipe — GPU unavailable for rendering, available for inference)
```

**GPU note:** Gazebo renders in software (llvmpipe). The RTX is used
exclusively by Ollama for LLM inference. Do not try to force GPU rendering in
Gazebo without prior confirmation.

---

## Tech stack

| Layer | Technology | Version | Notes |
|------|-----------|---------|-------|
| ROS 2 | Jazzy Jalisco | LTS 2024 | Workspace: ~/robot_ws |
| Simulator | Gazebo Harmonic | gz-sim 8 | Integrated with ros-jazzy |
| Robot | TurtleBot3 Waffle | — | LIDAR + camera (own model copy at 640×480, ADR-009) |
| Planner LLM | Qwen2.5-7B-Instruct | Ollama | Port 11434, temperature=0 |
| Plan check | plan_validation.py | robot_brain | Every navigate checked before execution: known zone, known point, no borrowed place (ADR-032) |
| SQL place memory (experiment) | sql_memory.py | robot_brain | `memory_source: sql` — Qwen writes a read-only SELECT over a SQLite copy of the places; measured against RAG, not the default (ADR-033) |
| Perception | Scene descriptor | robot_skills | Classical colors+clutter, no ML (ADR-014) |
| Embeddings | bge-m3 | Ollama | Multilingual, for ChromaDB (ADR-014 / rag-analysis §2.4) |
| Vector DB | ChromaDB | pip | Persistent at ~/robot_ws/data/chroma_db |
| Zones store | SQLite (stdlib) | — | ~/robot_ws/data/zones.db (ADR-011) |
| Room meaning | Static table | robot_zones | Room name → what it is for, ES+EN (ADR-022) |
| Navigation | Nav2 | ros-jazzy | SimpleCommander API |
| SLAM | SLAM Toolbox | ros-jazzy | Live mapping (ADR-004). A saved map loads read-only instead: map_server + AMCL, no SLAM, with the run starting in a named zone (`start_zone`, ADR-035). `saved_map_mode:=mapping` keeps mapping from it |
| Middleware | CycloneDDS | ros-jazzy | Pinned to loopback via cyclonedds.xml (ADR-006) |
| Velocity arbitration | cmd_vel_mux_node | robot_skills | Single owner of /cmd_vel: manual driving over Nav2 (ADR-029) |
| Dashboard | FastAPI + uvicorn | pip | http://localhost:8080 — live floor plan, goals, memory viewer (ADR-024/030), WASD driving (ADR-023) |
| Browser tests | Playwright + Chromium | pip | Dashboard page tested headless in layer 1 (ADR-030) |

No agent framework: skills are dispatched directly (`robot_brain/toolkit.py`).
LangChain was removed as vestigial (ADR-012); a real agent loop is roadmap.

---

## Project structure

```
~/robot_ws/
├── src/
│   ├── robot_interfaces/   # Custom ROS 2 msgs/srvs (build first)
│   ├── robot_zones/        # Shared SQLite store of named zones + room meanings
│   ├── robot_rag/          # ChromaDB + embeddings + semantic memory
│   ├── robot_skills/       # Executable nodes: nav, explore, perceive, report; cmd_vel mux
│   ├── robot_brain/        # LLM planner (cognitive core) + pre-execution plan check
│   ├── robot_dashboard/    # Web dashboard: observability, goals, RAG viewer, teleop
│   └── robot_bringup/      # Launch files for the whole system
├── eval/                   # RAG ablation benchmark (seed, run, score, report)
│                           #   scoring.py is pure logic — no ROS, unit-tested
├── tests/                  # Layer 1: pure-logic pytest suite (runs without ROS)
├── src/*/test/             # Layer 2: node-level tests (need ROS, run by colcon test)
├── data/
│   ├── chroma_db/          # Persistent vector DB (do NOT commit)
│   ├── knowledge/          # Static RAG documents (DO commit) — planner input (ADR-031)
│   └── logs/               # Task history logs (do NOT commit)
├── agent_env/              # Python venv (do NOT commit)
├── docs/
│   ├── architecture.md
│   ├── api_reference.md
│   ├── rag-pipeline.md     # What the RAG stores, how it embeds and retrieves
│   ├── rag-analysis.md     # Whether it helps — the measured ablation
│   ├── EVALUATION.md       # How to reproduce every benchmark number
│   ├── REVIEW.md           # Engineering review / audit trail
│   └── decisions/          # ADRs — Architecture Decision Records
├── .github/workflows/      # CI (lint + tests, no-ROS job + ROS job)
├── .devcontainer/          # VS Code devcontainer (same image as the Dockerfile)
├── Dockerfile              # Build + lint + both test layers, no sim (ADR-021)
├── docker-entrypoint.sh    # Sources setup_env.sh; `verify` = the whole check
└── requirements.txt
```

---

## Code conventions

### Python

```python
# Imports: stdlib → third-party → ROS 2 → local project
import json
from pathlib import Path

import chromadb
import ollama

import rclpy
from rclpy.node import Node

from robot_interfaces.srv import QueryRAG
```

- **Type hints required** on all public functions
- **Docstrings in English** — Google style
- **Logging:** `self.get_logger()` in ROS 2 nodes, never `print()`
- **ROS 2 node names:** snake_case with `_node` suffix (e.g. `llm_planner_node`)
- **Topic names:** `/robot/<name>` for project topics
- **Service names:** `/<package>/<name>` (e.g. `/rag/query`, `/skills/execute`)
- **Concurrency:** never nest `rclpy.spin_*` inside callbacks and never use
  throwaway executors — MultiThreadedExecutor + callback groups + Event waits
  (ADR-007)

### Mandatory docstring on every ROS 2 node

```python
class LLMPlannerNode(Node):
    """ROS 2 node that receives natural language goals and produces execution plans.

    Subscribes:
        /robot/goal (std_msgs/String): Natural language task description.

    Publishes:
        /robot/status (std_msgs/String): Current execution status.

    Services (client):
        /rag/query (QueryRAG): Retrieve context from ChromaDB.
        /skills/execute (ExecuteSkill): Dispatch skills to robot_skills package.

    Parameters:
        ollama_base_url (str): Ollama server URL. Default: http://localhost:11434
        llm_model (str): Model name for task planning. Default: qwen2.5:7b
        max_plan_steps (int): Maximum steps in a single plan. Default: 10
    """
```

### ROS 2 messages and services

Every `.msg` and `.srv` field gets a comment:

```
# QueryRAG.srv
string query_text        # Natural language query to search in ChromaDB
string collection_name   # Target collection: semantic_map | knowledge_base | task_history
int32  top_k             # Number of results to return (default: 5)
---
string[]  contexts       # Retrieved text fragments, ordered by relevance
float32[] scores         # Cosine similarity scores [0.0, 1.0]
bool      success        # False if collection not found or query failed
string    error_msg      # Empty string if success=True
```

---

## Documentation — strict rules

Documentation is **first-class** here. Every relevant change updates the
corresponding docs in the same commit.

1. **Every ROS 2 node** → full docstring with subs/pubs/srvs/params
2. **Every public function** → Google-style docstring (Args, Returns, Raises)
3. **Every architecture decision** → ADR in `docs/decisions/`
4. **Every new integration** → section in `docs/architecture.md`
5. **Every stack change** → update this CLAUDE.md

### ADR format

One file per significant decision in `docs/decisions/`:

```markdown
# ADR-NNN: Title

**Date:** YYYY-MM-DD
**Status:** Accepted

## Context
What problem forced a decision.

## Decision
What was chosen.

## Rationale
Why, including alternatives rejected.

## Consequences
Trade-offs accepted, follow-ups created.
```

### `docs/api_reference.md` — update with every new srv/msg/topic/param.

---

## Frequent commands

```bash
# Prepare the shell for building or launching (do NOT activate agent_env
# manually for ROS nodes — see docs/decisions/ADR-003-venv-pythonpath-bridge.md)
source ~/robot_ws/setup_env.sh

# Build the whole workspace
cd ~/robot_ws && colcon build --symlink-install

# Build a single package. REQUIRED after every Python, launch or config edit:
# --symlink-install installs copies here, not links (if in doubt, clean first:
# rm -rf build/<pkg> install/<pkg>)
colcon build --symlink-install --packages-select robot_rag

# Launch the full simulation (headless Gazebo + Nav2 + SLAM + agent + dashboard + RViz)
ros2 launch robot_bringup full_system.launch.py

# Send a task to the robot
ros2 topic pub --once /robot/goal std_msgs/String "data: 'Go to the kitchen and tell me what you see'"

# Watch the robot's response
ros2 topic echo /robot/response

# RAG vs LLM → SQL on the same places (ADR-033): export memory, then flip the source
cd eval && python3 export_places_sql.py && ros2 param set /llm_planner_node memory_source sql

# The plan as it will run — with raw_steps + plan_corrections when the check
# replaced a step (ADR-032). Turn the check off to see the planner unchecked:
ros2 topic echo /robot/plan
ros2 param set /llm_planner_node plan_validation false

# Web dashboard (auto-launched with agent.launch.py / full_system.launch.py)
#   http://localhost:8080

# Test the RAG service
ros2 service call /rag/query robot_interfaces/srv/QueryRAG \
  "{query_text: 'where is the kitchen', collection_name: 'knowledge_base', top_k: 3}"

# Look inside the memory as a human (ids + metadata + scores; empty query_text
# browses without embedding anything). The dashboard's memory panel reads this.
ros2 service call /rag/inspect robot_interfaces/srv/InspectMemory \
  "{collection_name: 'semantic_map', query_text: '', limit: 5, active_map_only: true}"

# Fold duplicate scene memories stored before merge-on-write (ADR-025). Stack
# stopped; dry run first, --apply backs data/chroma_db up before writing.
ros2 run robot_rag compact_memory            # read what it would fold
ros2 run robot_rag compact_memory --apply    # then do it
# Move memories written under an old session id to the frame's id (ADR-028)
ros2 run robot_rag compact_memory --apply --retag-session OLD fresh_turtlebot3_house_x-2.00_y-0.50

# Drive from a terminal instead of the dashboard: publish to the mux input,
# never straight to /cmd_vel (ADR-029)
ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args -p stamped:=true -r cmd_vel:=/robot/cmd_vel_manual

# Map the house by hand first: drive with WASD in the dashboard, name each room
# with "Nombrar esta habitación", then "Guardar mapa". Manual driving outranks
# Nav2 through cmd_vel_mux_node (ADR-029); use_nav2:=false keeps Nav2 out of it.
ros2 launch robot_bringup full_system.launch.py use_nav2:=false

# Start from a saved map (data/maps/<id>, or shipped in robot_bringup/maps/<id>):
# SLAM keeps mapping from it and the memory session is pinned to the same id
# (ADR-026); the file on disk only changes when you save again
ros2 launch robot_bringup full_system.launch.py saved_map:=house
# Load it read-only instead — map_server + AMCL, no SLAM ("Guardar mapa" then
# has nothing to save). Measured worse at navigating a hand-made map (ADR-035)
ros2 launch robot_bringup full_system.launch.py saved_map:=house saved_map_mode:=localization

# Demo setup: Gazebo window + RViz + dashboard on map `house`, read-only, robot
# starting in the `entrada` zone. The Gazebo window costs real-time factor
ros2 launch robot_bringup demo.launch.py
ros2 launch robot_bringup demo.launch.py use_gz_gui:=false           # RTF 0.90 measured
# Start somewhere else, or where the map begins (the spawn nook): start_zone:=''
ros2 launch robot_bringup demo.launch.py start_zone:=cocina

# Layer 1 — pure logic, no ROS needed (416 tests, 8 of them drive the dashboard
# page in headless Chromium — once: python3 -m playwright install chromium) + lint
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/
ruff check .

# Layer 2 — node level, needs a sourced workspace (28 tests). The env var is
# required: Jazzy's launch_testing pytest plugin breaks collection (ADR-018).
source ~/robot_ws/setup_env.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test && colcon test-result --all

# Benchmark suite (see docs/EVALUATION.md)
cd eval && python3 seed_memory.py && python3 run_benchmark.py tasks_full.yaml && python3 report.py full
# Reproduce WITHOUT a simulator (ADR-020): agent.launch.py + offline seed, Ollama only
cd eval && python3 seed_memory.py --offline && python3 run_benchmark.py tasks_full.yaml && python3 report.py full

# Reproduce the offline verification in a clean container (ADR-021) — needs no
# ROS 2, no Python and no GPU on the host. Expect ruff clean + 416 + 28, exit 0.
# The simulator is deliberately NOT in the image; Gazebo/RViz stay on the host.
docker build -t robot-rag-agent . && docker run --rm robot-rag-agent

# Ollama status
ollama ps
curl -s http://localhost:11434/api/tags | python3 -m json.tool
```

---

## Important environment variables

All of these are exported by `source setup_env.sh`.

```bash
# Project — ROBOT_WS is load-bearing (ADR-015): setup_env.sh resolves its own
# directory, and every node builds its default data paths from this variable.
# Never hardcode an absolute path; derive it from ROBOT_WS.
ROBOT_WS=<resolved from setup_env.sh's location>
CHROMA_DB_PATH=${ROBOT_WS}/data/chroma_db
KNOWLEDGE_DIR=${ROBOT_WS}/data/knowledge

# ROS 2
ROS_DOMAIN_ID=0
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp            # Best middleware for WSL2
CYCLONEDDS_URI=file://${ROBOT_WS}/cyclonedds.xml # DDS pinned to loopback (ADR-006)
TURTLEBOT3_MODEL=waffle

# Ollama
OLLAMA_KEEP_ALIVE=-1                             # Never evict models from VRAM
```

In node code, path parameters follow this pattern (never a literal path):

```python
WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))
self.declare_parameter('zones_db', str(WS_ROOT / 'data' / 'zones.db'))
```

---

## Known limitations and workarounds

| Problem | Cause | Workaround |
|----------|-------|------------|
| Gazebo renders on llvmpipe | Incomplete WSL2 GPU passthrough | Gazebo GUI off by default. Measured 2026-09-17 with the dashboard and RViz open: RTF **0.90** after dropping the camera to 5 Hz and removing the voxel layers (ADR-037); it was 0.47 before, and everything moved at half speed. Older figures (~0.68 headless, ~0.59 with the window) predate that change. The dashboard shows the live factor; `use_rviz:=false` buys more |
| Closing the Gazebo GUI killed everything | on_exit_shutdown:true on the stock gzclient include | Own simulation.launch.py launches server/GUI separately, GUI without shutdown |
| Map shows a second, rotated copy of the house | Two Gazebo servers: closing a launch's terminal (SIGHUP) leaves `ruby gz sim` running, and its robot's scans, odometry and clock reach the new SLAM through the bridge | `simulation.launch.py` refuses to start next to one and prints the PIDs to `kill` (ADR-034). Stop launches with Ctrl+C, not by closing the terminal |
| NPU unreachable in WSL2 | WSL2 doesn't expose the NPU device | Reserved for native Windows (voice phase: Whisper) |
| SLAM drift on long runs | Software-rendered sim | Save the map (dashboard "Guardar mapa" / `save_map` skill) and relaunch with `saved_map:=<id>` (ADR-026): it loads read-only, so drift cannot reach the saved map (ADR-035) |
| A loaded map's walls smeared after a room tour | `saved_map_mode:=mapping`: every scan goes into the graph, and the hand-made map is 0.3–0.5 m out in places, so SLAM draws the true geometry next to the saved walls | Not the default any more: a saved map loads read-only (map_server + AMCL), so nothing can change it, and `start_zone` makes that navigable — 3/3 demo goals against 0/3 from the spawn nook (ADR-035) |
| No plan for any goal, robot never moves | The run started where the map begins — in this house a nook 0.25 m from a wall — and no path leaves it at a correct robot radius | `start_zone:=entrada` (the `demo` launch does it by default): spawns the robot in that zone and tells SLAM or AMCL where it is (ADR-035). A fresh map has no zones yet, which is why the radius stays 0.20 (ADR-036) |
| "Guardar mapa" said it saved and did not | SLAM Toolbox's localization mode answers `serialize_map` with `result=0` and writes nothing | `save_map` now verifies the four files (posegraph, data, yaml, pgm) by modification stamp and fails loudly (ADR-035) |
| The robot wedges against a wall and the map goes bad | Nav2's `robot_radius` was 0.15 (TurtleBot3's Jazzy file) against the model's real 0.237 → it grazes walls, the wheels slip, odometry gains up to 99° of heading error and SLAM 0.8 m | `robot_radius: 0.20` — the largest value this house still plans with (ADR-036; on the same drive 0.24 took SLAM's error from 0.79 m to 0.06 m, but 0.22 and up cannot plan out of the spawn pose). Manual driving does not go through Nav2: do not push into furniture while mapping by hand |
| Intermittent DDS discovery | WSL2 multi-NIC (eth0/docker0) | CycloneDDS pinned to lo — cyclonedds.xml + CYCLONEDDS_URI (ADR-006) |
| Gazebo window doesn't appear | Dead msrdc.exe (WSLg bridge) | `wsl --shutdown` from PowerShell and relaunch |
| Goals outside the SLAM map | Map grows with exploration | Explore first, or start from a saved map (`saved_map:=house`); Nav2 rejects "outside bounds" goals |
| RViz floods the terminal with "controller_server service not available … Retrying" | Nav2's RViz panels poll for servers that `use_nav2:=false` never starts | Fixed: without Nav2, RViz opens `robot_bringup/rviz/mapping.rviz` (no Nav2 panels). Harmless if seen on an old build |
| Autonomous explore maps little of a big house | 3.5 m LIDAR leaves large rooms unknown; the old nearest-frontier rule hugged walls | Explore now targets frontier clusters (~40–55% more area measured, ADR-027); for a full map, drive with WASD and save it (ADR-023, ADR-026) |
| End-to-end navigation unreliable | Narrow doorways + software physics | Benchmark measures the planning decision (ADR-013) |
| "Ve a estacion_d" (plausible but nonexistent) drove to a *real* station | With RAG the 7B planner invents a zone or reuses retrieved coordinates for a name no memory holds (hard suite 0/6 with the check off) | Plan check before execution replaces such steps with explore: 6/6 (ADR-032). It is a model call: it wrongly rejected 2 correct spatial answers. Never "repair" a memory named as a zone into coordinates — measured, it let a borrowed place through |
| Spatial goals ("the station nearest the base") | The planner compares coordinates unreliably, even with every coordinate and the zone's centre in the prompt | Open: right station 6/6 in one session, 0/6 in the next (rag-analysis §2.6). Candidate fixes: compute the relation in code, or the agent loop |
| "Is RAG better than SQL?" | The same-data comparison is one session on 8–12 places, with a vocabulary the SQL prompt lists | Tied on direct lookups; RAG recovered the right position on spatial tasks 3/6 vs SQL 0/6 (ADR-033). Present it as a signal from a small experiment, not a settled result; extend before claiming more |
| Benchmark totals move between sessions | `temperature=0` is repeatable, not exact | Claim effects only from conditions compared inside one session (the runner does rag / norag / rag_unchecked on one memory); ±2–3 runs between sessions is noise |
| Python, launch or config edits not taking effect | In this workspace `--symlink-install` installs *copies* of Python modules, launch files and config YAML, not links | Rebuild after every Python edit (`colcon build --symlink-install --packages-select <pkg>`); if in doubt `rm -rf build/<pkg> install/<pkg>` first. Before a live measurement, check the installed module matches `src/` — a published run was once invalidated by this (ADR-027) |

---

## Workflow for new features

1. Branch: `git checkout -b feature/descriptive-name`
2. Implement
3. Write/update docstrings and docs
4. Write an ADR if there is an architecture decision
5. Manual test with `ros2 service call` / `ros2 topic pub`
6. Update `docs/api_reference.md` for new srv/msg/topics/params
7. `pytest tests/` + `ruff check .` green; `colcon test` too if you touched a node
   — and if the change is in a node's *logic*, ask whether it belongs in a
   pure-logic module that layer 1 can cover (ADR-018)
8. Commit with a descriptive English message:
   ```
   feat(robot_rag): add semantic_map collection with pose indexing
   ```

---

## Do NOT

- **No `print()`** in ROS 2 nodes — use `self.get_logger().info()`
- **No hardcoded paths** — ROS 2 parameters or env vars
- **Do not commit** `data/chroma_db/`, `data/logs/`, `data/zones.db`, `agent_env/`
- **Do not modify** `/opt/ros/jazzy/` — system installation (copy into the repo instead, like the waffle model)
- **Do not forget** `source ~/robot_ws/install/setup.bash` after `colcon build`
- **No public functions without docstrings**
- **No nested `rclpy.spin_*` or throwaway executors** (ADR-007)
- **Do not edit `data/knowledge/` without re-running the planning suites** — its
  chunks land in the planner prompt; one worked example once overrode the
  "explore, don't guess" rule (3/3 → 0/3). `rag_node` re-syncs the collection on
  start; the numbers do not update themselves (ADR-031, EVALUATION.md §5b).
  Never write benchmark goals or landmark names into it
- **Do not change the scene descriptor's phrases or colour names** without
  updating `PLACES_SQL_SYSTEM_PROMPT` (prompts.py): the SQL memory experiment
  only matches because its prompt lists that vocabulary (ADR-033)
- **No `main()` that catches only `KeyboardInterrupt`** — catch
  `ExternalShutdownException` too and guard `rclpy.shutdown()` with
  `rclpy.ok()`, or every `ros2 launch` stop prints a traceback (ADR-016)

---

## Qwen robotics models — integration status

> See `docs/decisions/ADR-002-qwen-robot-suite.md` (corrected 2026-09-16).

| Model | Public weights | Integrable now | What this project uses instead |
|--------|---------------|------------------|--------------------|
| Qwen-RobotNav (4B / 8B, on Qwen3-VL) | ❌ No release planned (per its repo) | ❌ No | Nav2 + classical scene descriptor (ADR-014) |
| Qwen-RobotManip | ❌ No release planned (per its repo) | ❌ No | N/A (no manipulation) |
| Qwen2.5-7B | ✅ Available | ✅ Yes | The planner |
| Qwen2.5-VL-7B | ✅ Available | Removed | Unreliable on software-rendered frames (ADR-014) |

There is no "Qwen-RobotWorld" (Qwen-AgentWorld is a language world model for
software agents). Do not plan work around robotics weights that Qwen says it
will not release; re-check https://github.com/QwenLM/Qwen-RobotNav only if that
statement changes.
