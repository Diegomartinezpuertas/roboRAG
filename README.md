# Robot RAG Agent

A cognitive agent for a mobile robot (TurtleBot3) running in simulation
(ROS 2 Jazzy + Gazebo Harmonic). It takes natural-language goals, consults a
semantic memory (RAG over ChromaDB), plans with a local LLM (Qwen2.5-7B via
Ollama), and executes the plan through ROS 2 skills (navigation, exploration,
perception, reporting). A FastAPI dashboard gives live observability, an
interactive SLAM map, and text/voice goal input.

[![CI](https://github.com/Diegomartinezpuertas/roboRAG/actions/workflows/ci.yml/badge.svg)](https://github.com/Diegomartinezpuertas/roboRAG/actions/workflows/ci.yml)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![ROS 2 Jazzy](https://img.shields.io/badge/ROS_2-Jazzy-blue)

> Personal portfolio project. Documentation is treated as first-class: every
> significant decision is recorded as an ADR in [`docs/decisions/`](docs/decisions).

---

## Does the RAG actually help? (measured)

The central question of this project is whether semantic memory (RAG) improves
natural-language navigation. Rather than assert it, it is **measured** with an
ablation: the same tasks are run with RAG on and off, and the planner's
decision is scored. The measurement is at the **planning level** (does the
robot decide to navigate directly to a remembered location, vs. explore
blindly) — this is the causal mechanism of the hypothesis, and it is
reproducible (`temperature=0`), unlike end-to-end navigation on this
software-rendered WSL2 sim (see [ADR-013](docs/decisions/ADR-013-planning-level-benchmark.md)).

**Result** (`eval/tasks_full.yaml`, 42 runs):

| Task type | With RAG | Without RAG |
|---|---|---|
| Object-referenced nav (RAG-dependent) | **9/9 (100%)** | **0/9 (0%)** |
| Description-referenced nav (self-built memory) | **6/6 (100%)** | **0/6 (0%)** |
| Known-zone nav (control) | 3/3 (100%) | 3/3 (100%) |
| Impossible goal (hallucination check) | 3/3 (100%) | 3/3 (100%) |

![Benchmark results](eval/results/benchmark.png)

More measured findings (full data and charts in
[docs/rag-analysis.md](docs/rag-analysis.md)):

- **Phrasing/language robustness** (36 runs): retrieval survives Spanish
  paraphrases and English goals — 18/18 direct-nav with RAG (with the bge-m3
  embedder) vs 0/18 without.
- **The embedding model is a real multilingual bottleneck**: with Spanish
  queries over English memories, nomic-embed-text ranks the right document
  first only **43%** of the time (negative separation margin), while
  **bge-m3 reaches 86%** with a positive margin — both stay 100% in English.
  bge-m3 is the project default as a result.
- **RAG's latency cost is measurable but small**: 2.2 s vs 1.8 s mean
  goal→plan (three collection retrievals with bge-m3), dwarfed by LLM
  inference either way.

![Embedding comparison](eval/results/embeddings.png)

How to read this — it is deliberately not "RAG is magic":

- **Object-referenced navigation** ("go to *estacion_a*", a place stored in
  semantic memory): RAG makes all the difference. With it, the planner
  retrieves the coordinates and navigates directly; without it, it has no idea
  where the place is and falls back to exploring.
- **Description-referenced navigation** ("go to the white, open room"): the
  memory here is **self-built** — the robot stores a classical scene
  description (dominant colors + LIDAR clutter, no ML) of every place it
  reaches while exploring. Several remembered areas can legitimately match a
  description, so the scorer accepts any retrieved area whose stored text
  matches the requested attributes.
- **Known-zone navigation** (a place stored in a plain SQLite table): both
  conditions succeed. This control shows the ablation isolates RAG's *object
  memory* specifically — when the information is available another way, RAG is
  not needed. At this scale a lookup would often suffice; RAG earns its keep
  as the remembered vocabulary grows.
- **Impossible goal** ("go to the garage", which does not exist): neither
  condition invents coordinates. RAG does not cause hallucination, and its
  absence does not either.

Reproduce everything (suites, charts, embedding comparison) with
[docs/EVALUATION.md](docs/EVALUATION.md); the full interpretation — when RAG
wins, when plain SQL wins, when an LLM→SQL design would be better — is in
[docs/rag-analysis.md](docs/rag-analysis.md).

**How the memory itself works** — what is stored in each of the three
collections, where the content comes from, how it is chunked and embedded, and
how a retrieved fragment ends up in the planning prompt — is documented in
[docs/rag-pipeline.md](docs/rag-pipeline.md).

---

## Architecture

```mermaid
flowchart TD
    user["User (text / voice)"] -->|/robot/goal| planner
    dash["robot_dashboard<br/>(FastAPI @ :8080)"] -->|/robot/goal| planner
    planner["robot_brain<br/>llm_planner_node"] -->|/rag/query| rag
    planner -->|prompt| ollama["Qwen2.5-7B<br/>(Ollama)"]
    rag["robot_rag<br/>rag_node"] --> chroma[("ChromaDB<br/>semantic_map · knowledge_base · task_history")]
    planner -->|/skills/execute| skills["robot_skills<br/>skills_executor_node"]
    skills -->|navigate / explore| nav2["Nav2 + SLAM Toolbox<br/>Gazebo (TurtleBot3)"]
    skills -->|/rag/update_map| rag
    skills -->|/robot/response| dash
    zones[("robot_zones<br/>SQLite zones.db")] --- planner
    zones --- skills
    zones --- dash
```

| Package | Role |
|---------|------|
| `robot_interfaces` | Custom messages/services (`QueryRAG`, `ExecuteSkill`, `UpdateMap`, `SemanticObject`) |
| `robot_rag` | ChromaDB-backed semantic memory + RAG query service |
| `robot_skills` | Executable skills: navigate (Nav2), explore (frontier, self-building memory), perceive (scene descriptor), scan_360 (in-place panoramic sweep), report |
| `robot_brain` | LLM planner: RAG retrieval → Qwen plan → skill dispatch → post-execution report |
| `robot_zones` | Shared SQLite store of user-defined named zones |
| `robot_dashboard` | Web dashboard: observability, interactive SLAM map, text/voice goals |
| `robot_bringup` | Launch files and configuration for the whole system |

---

## The cognitive loop

1. A goal arrives on `/robot/goal` (typed, spoken via the dashboard, or `ros2 topic pub`).
2. `llm_planner_node` retrieves relevant context from the three RAG collections
   (dropping low-relevance hits below a similarity threshold) and the known zones.
3. Qwen2.5-7B produces a JSON plan. If retrieved context contains coordinates,
   it navigates directly; otherwise it explores.
4. Skills execute in sequence via `/skills/execute`. The memory is
   **self-building**: every place reached while exploring is described
   (dominant colors + LIDAR clutter) and stored with its coordinates, so
   goals like "go to the white, open room" resolve later without seeding.
5. A **second** LLM call summarizes the *actual* results (grounded, in the
   user's language) and publishes it to `/robot/response`.

---

## Quickstart

Requires ROS 2 Jazzy, Gazebo Harmonic, and Ollama with `qwen2.5:7b` and
`bge-m3` pulled. See
[`CLAUDE.md`](CLAUDE.md) for the full environment (WSL2 + venv bridge).

The workspace is not pinned to a fixed location: `setup_env.sh` derives its own
directory and exports it as `ROBOT_WS`, which every node uses to build its
default data paths. Clone it wherever you like.

```bash
# Prepare the shell (sources ROS 2, exports ROBOT_WS, bridges the venv via
# PYTHONPATH — ADR-003)
source <your-clone>/setup_env.sh

# Build
cd "$ROBOT_WS" && colcon build --symlink-install

# Launch everything: Gazebo + Nav2 + SLAM + agent + dashboard + RViz
ros2 launch robot_bringup full_system.launch.py

# Send a goal
ros2 topic pub --once /robot/goal std_msgs/String "data: 'Explora el entorno durante 60 segundos'"

# Dashboard (open in the Windows browser): http://localhost:8080
```

---

## Testing & CI

Three layers, each defined by what it needs to run
([ADR-018](docs/decisions/ADR-018-test-strategy.md)):

**1. Pure logic — 120 tests, no ROS required.**
RAG chunking, plan parsing, prompt building and language detection, frontier
selection, the scene descriptor, the SQLite zone store, the ChromaDB wrapper,
the dashboard's HTTP layer, and the benchmark plan scorer.

```bash
pytest tests/
```

**2. Node level — 21 tests, needs a ROS 2 install.**
The real nodes on real executors: `/skills/execute` called over a real service
client (dispatch, malformed input, executor survival), the dashboard's
HTTP↔ROS bridge (a POSTed goal arriving on `/robot/goal` as a real message),
and the shutdown contract of every node (spawn, SIGINT, assert a clean silent
exit — this one is a direct guard on [ADR-016](docs/decisions/ADR-016-node-shutdown-contract.md)).

```bash
source setup_env.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test && colcon test-result --all
```

> The env var is required: the `launch_testing` pytest plugin shipped with
> Jazzy is incompatible with current pytest and breaks collection.

**3. Manual — anything needing Gazebo, Nav2 or Ollama.**
End-to-end navigation, exploration, perception, and the planner's LLM calls.
Not faked, not claimed as covered — see "Honest limitations".

**Lint:** `ruff check .` (config in `ruff.toml`), the project's single linter
([ADR-017](docs/decisions/ADR-017-single-linter-ruff.md)).
**CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs layer 1 on a
plain runner and layer 2 in a `ros:jazzy-ros-base` container, on every push/PR.

---

## Tech stack

| Layer | Choice |
|-------|--------|
| Middleware | ROS 2 Jazzy, CycloneDDS (pinned to loopback for WSL2, [ADR-006](docs/decisions/ADR-006-cyclonedds-loopback.md)) |
| Simulation | Gazebo Harmonic, TurtleBot3 Waffle |
| Navigation | Nav2 (SimpleCommander) + SLAM Toolbox ([ADR-004](docs/decisions/ADR-004-slam-toolbox-no-amcl.md)) |
| Planner LLM | Qwen2.5-7B via Ollama (`temperature=0`) |
| Perception | Classical scene descriptor — camera colors + LIDAR clutter, no ML ([ADR-014](docs/decisions/ADR-014-classical-scene-descriptor.md)) |
| Embeddings | bge-m3 via Ollama (multilingual; chosen over nomic-embed-text on measured data) |
| Vector store | ChromaDB ([ADR-001](docs/decisions/ADR-001-chromadb.md)) |
| Dashboard | FastAPI + uvicorn, vanilla-JS SPA ([ADR-005](docs/decisions/ADR-005-dashboard-fastapi.md)) |

Design decisions are logged as [19 ADRs](docs/decisions/). Highlights:
[ADR-007](docs/decisions/ADR-007-executors-callback-groups.md) (executor/
callback-group design behind the blocking service calls),
[ADR-009](docs/decisions/ADR-009-camera-resolution-bridge.md) (a 1080p camera
silently dropping frames over DDS),
[ADR-011](docs/decisions/ADR-011-rag-quality-zones-sqlite.md) and
[ADR-012](docs/decisions/ADR-012-navigable-rag-post-execution-report.md)
(making the RAG genuinely navigable).

---

## Honest limitations

- **Benchmark is at the planning level, not physical SR/SPL.** End-to-end
  navigation on this WSL2/software-rendered sim is unreliable (the robot wedges
  in narrow doorways; `navigate` occasionally reports false success). The
  planning-level metric measures the RAG mechanism reproducibly; a physical
  SR/SPL run on better hardware is future work — the end-to-end harness
  (reset-to-home, odometry integration, SPL) is written and ready in
  `eval/run_benchmark.py`'s history.
- **The headline suite is saturated** (every cell 100% or 0%), so it cannot
  measure an improvement — the roadmap's agent loop would score identically.
  `eval/tasks_hard.yaml` (60 runs) exists for that reason, built to be failable
  by the current system. Measured result: **27/30 with RAG vs 3/30 without**.
  It does leave headroom, and points precisely at where — the planner solves
  disambiguation (9/9) and ordered multi-step plans (9/9) but only **half of
  the spatial-reasoning tasks** ("go to the station nearest the base", 3/6),
  which is the clearest target for the agent-loop work. Full breakdown in
  [docs/rag-analysis.md §2.6](docs/rag-analysis.md).
- **RAG's value is scale-dependent.** For a handful of places, a SQLite lookup
  covers most of it (see the known-zone control). RAG matters as remembered,
  open-vocabulary memory grows.
- **Perception is attribute-level, not object-level.** The VLM was removed
  (unreliable on software-rendered frames, [ADR-014](docs/decisions/ADR-014-classical-scene-descriptor.md));
  the classical descriptor characterizes places (colors, clutter) but cannot
  name objects — that needs a detector (roadmap).
- **Cross-lingual retrieval quality depends heavily on the embedder.** With
  nomic-embed-text, Spanish queries over English docs hit only 43% top-1;
  the default is now bge-m3 (86% measured), with a relevance threshold as a
  noise floor — see [docs/rag-analysis.md](docs/rag-analysis.md) §2.4.

## Roadmap

1. **Real agent loop** (replanning from execution feedback) — the jump from
   plan-then-execute to a true agent; expected benchmark improvement and a
   natural next README section.
2. **Native voice phase** (Whisper on the Windows NPU, publishing to
   `/robot/goal`) — the NPU is unreachable from WSL2, so this runs host-side.
3. **Object-level detection** (YOLOv8n; VLM revisit on real-camera hardware)
   — the classical descriptor covers place attributes, naming objects needs
   a detector.
4. **SLAM map save/load** (`map_saver_cli`) for reproducible scenarios and a
   physical SR/SPL benchmark run.
5. **Docker/devcontainer** for full reproducibility (kills the "works on my
   WSL2" caveat).

## License

MIT — see [LICENSE](LICENSE).
