# EVALUATION — running the full benchmark suite

Everything needed to reproduce the numbers and charts in
[rag-analysis.md](rag-analysis.md) and the README, from a cold start.
Total time: **~25–35 min**, most of it waiting for exploration and LLM calls.

## 0. Prerequisites (once)

```bash
# Ollama models
ollama pull qwen2.5:7b
ollama pull bge-m3            # default embedder
ollama pull nomic-embed-text  # only for the embedding comparison

# Python deps into the venv (see ADR-003 for why a venv + PYTHONPATH bridge)
source ~/robot_ws/agent_env/bin/activate && pip install -r requirements.txt && deactivate

# Build the workspace
source ~/robot_ws/setup_env.sh
cd ~/robot_ws && colcon build --symlink-install
```

Sanity checks: `curl -s localhost:11434/api/tags | grep qwen2.5` and
`ros2 pkg list | grep robot_` (7 packages).

## 1. Bring the system up

The **planning benchmark does not need Gazebo** (the planner runs in
`dry_run`), but seeding landmarks needs a SLAM map. Two options:

**Option A — full system (needed the first time, to build a map):**

```bash
source ~/robot_ws/setup_env.sh
ros2 launch robot_bringup full_system.launch.py     # Gazebo headless + Nav2 + SLAM + agent
```

Wait for `Managed nodes are active` and `rag_node ready` in the log. Then
grow the map (~2–3 min):

```bash
ros2 service call /skills/execute robot_interfaces/srv/ExecuteSkill \
  '{skill_name: "explore", params_json: "{\"duration_sec\": 150}"}'
```

**Option B — agent only (re-runs, when `data/chroma_db` + `eval/landmarks.json`
already exist from a previous session):**

```bash
ros2 launch robot_bringup agent.launch.py
```

## 2. Seed the memory

Registers 3 distinct landmarks in `semantic_map` (RAG-only), two contrasting
scene descriptors for the attribute tasks, and the control zone `base` in
SQLite. Writes `eval/landmarks.json` for the scorer.

```bash
cd ~/robot_ws/eval
python3 seed_memory.py
```

Expect three `seeded at (...) ok=True` lines. Requires the SLAM map
(Option A at least once).

## 3. Run the benchmark suites

```bash
python3 run_benchmark.py tasks_full.yaml        # 42 runs (~4 min): ablation + attribute + controls
python3 run_benchmark.py tasks_phrasing.yaml    # 36 runs (~4 min): phrasing/language robustness
```

Each writes `results/<suite>/{rag,norag}.json` (per-run records with the
decision, the raw plan, and goal→plan latency). The script flips
`rag_enabled` live with `ros2 param set` — no restarts, same memory state in
both conditions.

## 4. Embedding model comparison (no ROS needed)

```bash
python3 embedding_bench.py        # nomic-embed-text vs bge-m3, ES vs EN
```

Writes `results/embeddings.json` and prints the accuracy/margin table. Run it
*after* the LLM suites so the embedding calls don't pollute latency numbers.

## 5. Tables and charts

```bash
python3 report.py full            # markdown table -> results/full/report.md
python3 report.py phrasing
python3 plot_results.py           # results/{benchmark,phrasing,embeddings}.png
```

The PNGs are the ones embedded in the README and docs/rag-analysis.md.

## 6. Unit tests and lint (no ROS needed)

```bash
cd ~/robot_ws
source agent_env/bin/activate
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/    # 47 tests
ruff check .
```

(`PYTEST_DISABLE_PLUGIN_AUTOLOAD` avoids ROS-installed pytest plugins that
are incompatible with pytest ≥ 9; CI doesn't need it because CI has no ROS.)

## Changing conditions manually

```bash
ros2 param set /llm_planner_node rag_enabled false     # ablate RAG
ros2 param set /llm_planner_node dry_run true          # plan without driving
ros2 param set /llm_planner_node zones_in_prompt false # hide zone names too
```

All three are re-read per goal — no restart needed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No map received` in seed_memory | Sim not running or SLAM not up — use Option A |
| Plans never arrive (`no_plan`) | Check Ollama: `ollama ps`; first call after idle takes ~4 s extra (model load) |
| `Unknown zone: base` in the control task | Re-run `seed_memory.py` (zone lives in `data/zones.db`) |
| Landmarks empty after relaunch | `data/chroma_db` was deleted — re-run seeding |
| Code changes not taking effect | `rm -rf build/<pkg> install/<pkg>` and rebuild (stale symlink-install copies) |
| Process aborts at script exit | Fixed via `SpinHandle.stop()`; if it reappears, results are already written before teardown |
