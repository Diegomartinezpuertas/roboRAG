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

**Option C — no simulator at all (the fast path, [ADR-020](decisions/ADR-020-offline-benchmark-seeding.md)):**
seed fixed landmark coordinates offline, so a fresh clone reproduces the
planning suites with just Ollama and the agent nodes — no Gazebo, no SLAM.

```bash
ros2 launch robot_bringup agent.launch.py use_sim_time:=false   # rag + planner, no sim clock
# then seed with --offline in step 2
```

## 2. Seed the memory

Registers 3 distinct landmarks in `semantic_map` (RAG-only), two contrasting
scene descriptors for the attribute tasks, and the control zone `base` in
SQLite. Writes `eval/landmarks.json` for the scorer.

```bash
cd ~/robot_ws/eval
python3 seed_memory.py --reset-zones             # Option A/B: poses from the live map
python3 seed_memory.py --offline --reset-zones   # Option C: fixed poses, no sim needed
```

Expect three `seeded ... ok=True` lines. The default reads the SLAM map (needs
Option A at least once); `--offline` uses fixed coordinates and needs no map.
Either way seeding goes through `/rag/update_map`, so `rag_node` tags each memory
with the active map session ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md))
— that tagging is what makes retrieval return them.

> **Stale local memory?** A `data/chroma_db` seeded *before* ADR-019 holds
> untagged memories the current `rag_node` hides (retrieval returns nothing, so
> the benchmark reports 0/6 with RAG). Re-seeding — `--offline` is the quickest
> — re-registers the landmarks under the current session and fixes it.

> **Start from clean session state.** The numbers assume the memory contains
> only what seeding puts there. Two kinds of leftover on a development machine
> silently confound the measurement — a fresh clone has neither:
>
> - **Stale zones.** Every known zone name goes into the planner prompt, so a
>   leftover zone can capture a control goal. `--reset-zones` deletes all zones
>   but the control `base` before seeding; without the flag, `seed_memory.py`
>   warns if others exist.
> - **Stale task logs.** `data/logs/*.json` from earlier sessions hold absolute
>   coordinates from *old maps*, which retrieval will happily hand the planner
>   as if current (see [rag-pipeline.md §6](rag-pipeline.md)). Before a clean
>   run, empty them and rebuild the vector store:
>
>   ```bash
>   rm -f ~/robot_ws/data/logs/*.json
>   rm -rf ~/robot_ws/data/chroma_db    # re-ingested on next rag_node start
>   ```
>
> Skipping this is what makes the `zone_nav` control appear to fail — a
> session-state artefact, not a property of RAG.

## 3. Run the benchmark suites

```bash
python3 run_benchmark.py tasks_full.yaml        # 42 runs (~4 min): ablation + attribute + controls
python3 run_benchmark.py tasks_phrasing.yaml    # 36 runs (~4 min): phrasing/language robustness
```

Each writes `results/<suite>/{rag,norag}.json` (per-run records with the
decision, the raw plan, and goal→plan latency). The script flips
`rag_enabled` live with `ros2 param set` — no restarts, same memory state in
both conditions. It **reads the flag back and retries** before each condition;
if you see `WARNING: could not confirm ... rag_enabled=...`, DDS discovery has
not settled (common right after launch) — the run would be invalid, so wait a
few seconds after `rag_node ready` before starting, and re-run.

### 3b. The hard suite (has headroom)

`tasks_full.yaml` is **saturated**: every cell is 100% or 0%, so it cannot show
an improvement from here. `tasks_hard.yaml` is built to be failable by the
current system, which is what makes it useful for measuring the roadmap's agent
loop. It adds four task types: disambiguating between name-confusable memories,
holding an ordered multi-step plan, resolving a spatial relation over retrieved
coordinates, and declining a plausible-sounding place that does not exist.

It needs extra landmarks, which are **opt-in**:

```bash
python3 seed_memory.py --hard --reset-zones  # adds estacion_a_norte, estacion_c_sur
python3 run_benchmark.py tasks_hard.yaml     # 60 runs (~6 min)
python3 report.py hard
```

> **Why opt-in.** The distractors add competing entries to semantic memory,
> which changes retrieval for *every* query. Seeding them by default would
> silently invalidate the committed `tasks_full` results, which were measured
> against a three-landmark memory. Run plain `seed_memory.py` to reproduce the
> published numbers; add `--hard` only for this suite.

Measured result: **27/30 with RAG vs 3/30 without** (see
[rag-analysis.md §2.6](rag-analysis.md)). The per-run `decision` label is richer
than pass/fail — `visited_both`, `out_of_order`, `incomplete_2_of_3`,
`wrong_landmark`, `hallucinated` — so a failure says *how* the planner was
wrong. `plot_results.py` renders that breakdown as `results/hard_decisions.png`.

The scoring rules live in `eval/scoring.py` (pure logic, no ROS) and are
covered by `tests/test_benchmark_scoring.py`, so the suite's definition of
success is verified without needing a simulator (ADR-018).

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
                                  # + hard{,_decisions}.png when results/hard/ exists
```

The PNGs are the ones embedded in the README and docs/rag-analysis.md.

## 6. Tests and lint

Pure logic — no ROS needed (includes the benchmark scorer):

```bash
cd ~/robot_ws
source agent_env/bin/activate
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/    # 120 tests
ruff check .
```

Node level — needs a sourced workspace:

```bash
source ~/robot_ws/setup_env.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test && colcon test-result --all   # 21 tests
```

(`PYTEST_DISABLE_PLUGIN_AUTOLOAD` avoids the ROS-installed `launch_testing`
pytest plugin, which is incompatible with current pytest and breaks collection
outright. The no-ROS CI job doesn't need it; the ROS job sets it. See ADR-018.)

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
