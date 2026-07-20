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
| Perception | Scene descriptor | robot_skills | Classical colors+clutter, no ML (ADR-014) |
| Embeddings | bge-m3 | Ollama | Multilingual, for ChromaDB (ADR-014 / rag-analysis §2.4) |
| Vector DB | ChromaDB | pip | Persistent at ~/robot_ws/data/chroma_db |
| Zones store | SQLite (stdlib) | — | ~/robot_ws/data/zones.db (ADR-011) |
| Navigation | Nav2 | ros-jazzy | SimpleCommander API |
| SLAM | SLAM Toolbox | ros-jazzy | Live mapping (no AMCL, ADR-004) |
| Middleware | CycloneDDS | ros-jazzy | Pinned to loopback via cyclonedds.xml (ADR-006) |
| Dashboard | FastAPI + uvicorn | pip | http://localhost:8080 — observability + goals (ADR-005) |

No agent framework: skills are dispatched directly (`robot_brain/toolkit.py`).
LangChain was removed as vestigial (ADR-012); a real agent loop is roadmap.

---

## Project structure

```
~/robot_ws/
├── src/
│   ├── robot_interfaces/   # Custom ROS 2 msgs/srvs (build first)
│   ├── robot_zones/        # Shared SQLite store of named zones
│   ├── robot_rag/          # ChromaDB + embeddings + semantic memory
│   ├── robot_skills/       # Executable nodes: nav, explore, perceive, report
│   ├── robot_brain/        # LLM planner (cognitive core)
│   ├── robot_dashboard/    # Web dashboard: observability + goals (text/voice)
│   └── robot_bringup/      # Launch files for the whole system
├── eval/                   # RAG ablation benchmark (seed, run, score, report)
│                           #   scoring.py is pure logic — no ROS, unit-tested
├── tests/                  # Layer 1: pure-logic pytest suite (runs without ROS)
├── src/*/test/             # Layer 2: node-level tests (need ROS, run by colcon test)
├── data/
│   ├── chroma_db/          # Persistent vector DB (do NOT commit)
│   ├── knowledge/          # Static RAG documents (DO commit)
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

# Build a single package (if changes don't land, clean first:
# rm -rf build/<pkg> install/<pkg> — symlink-install sometimes leaves stale copies)
colcon build --symlink-install --packages-select robot_rag

# Launch the full simulation (headless Gazebo + Nav2 + SLAM + agent + dashboard + RViz)
ros2 launch robot_bringup full_system.launch.py

# Send a task to the robot
ros2 topic pub --once /robot/goal std_msgs/String "data: 'Go to the kitchen and tell me what you see'"

# Watch the robot's response
ros2 topic echo /robot/response

# Web dashboard (auto-launched with agent.launch.py / full_system.launch.py)
#   http://localhost:8080

# Test the RAG service
ros2 service call /rag/query robot_interfaces/srv/QueryRAG \
  "{query_text: 'where is the kitchen', collection_name: 'knowledge_base', top_k: 3}"

# Layer 1 — pure logic, no ROS needed (120 tests) + lint
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/
ruff check .

# Layer 2 — node level, needs a sourced workspace (21 tests). The env var is
# required: Jazzy's launch_testing pytest plugin breaks collection (ADR-018).
source ~/robot_ws/setup_env.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test && colcon test-result --all

# Benchmark suite (see docs/EVALUATION.md)
cd eval && python3 seed_memory.py && python3 run_benchmark.py tasks_full.yaml && python3 report.py full

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
| Gazebo renders on llvmpipe | Incomplete WSL2 GPU passthrough | Gazebo GUI disabled by default (RTF 0.15→~1.0); view via RViz (use_rviz:=true) or dashboard. use_gz_gui:=true if needed |
| Closing the Gazebo GUI killed everything | on_exit_shutdown:true on the stock gzclient include | Own simulation.launch.py launches server/GUI separately, GUI without shutdown |
| NPU unreachable in WSL2 | WSL2 doesn't expose the NPU device | Reserved for native Windows (voice phase: Whisper) |
| SLAM drift on long runs | Software-rendered sim | Save the map periodically with map_saver_cli |
| Intermittent DDS discovery | WSL2 multi-NIC (eth0/docker0) | CycloneDDS pinned to lo — cyclonedds.xml + CYCLONEDDS_URI (ADR-006) |
| Gazebo window doesn't appear | Dead msrdc.exe (WSLg bridge) | `wsl --shutdown` from PowerShell and relaunch |
| Goals outside the SLAM map | Map grows with exploration | Explore first; Nav2 rejects "outside bounds" goals |
| End-to-end navigation unreliable | Narrow doorways + software physics | Benchmark measures the planning decision (ADR-013) |
| Stale installs after edits | colcon symlink-install quirk | `rm -rf build/<pkg> install/<pkg>` then rebuild |

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
- **No `main()` that catches only `KeyboardInterrupt`** — catch
  `ExternalShutdownException` too and guard `rclpy.shutdown()` with
  `rclpy.ok()`, or every `ros2 launch` stop prints a traceback (ADR-016)

---

## Qwen Robot Suite — integration status

> See `docs/decisions/ADR-002-qwen-robot-suite.md` for the full analysis.

| Model | Public weights | Integrable now | Current alternative |
|--------|---------------|------------------|--------------------|
| Qwen-RobotNav-4B | ❌ Not released | ❌ No | Qwen2.5-VL-7B + Nav2 |
| Qwen-RobotManip | ❌ Not released | ❌ No | N/A (no manipulation) |
| Qwen-RobotWorld | ❌ Not released | ❌ No | N/A |
| Qwen2.5-VL-7B | ✅ Available | ✅ Yes | — |
| Qwen2.5-7B | ✅ Available | ✅ Yes | — |

**When weights are released** (watch https://github.com/QwenLM/Qwen-RobotNav):
replace `perceive_skill.py` + `nav_skill.py` with Qwen-RobotNav-4B calls
(camera images + instruction → waypoints, ~200ms per inference on Jetson-class
hardware per the published deployment numbers).
