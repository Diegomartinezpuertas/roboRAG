# EVALUATION — running the full benchmark suite

Everything needed to reproduce the numbers and charts in
[rag-analysis.md](rag-analysis.md) and the README, from a cold start.
Total time: **~25–40 min**, most of it waiting for exploration and LLM calls.

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
# No saved_map here: the published numbers were produced on a map built during
# the run, and a preloaded map would change what the robot has 'seen' (ADR-026).
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
> - **Stale zones.** Every known zone goes into the planner prompt, so a
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
>   rm -f ~/robot_ws/data/zones.db*     # see "Stale zone memories" below
>   ```
>
> - **Stale zone memories.** Since ADR-026 the dashboard indexes every zone in
>   `zones.db` into semantic memory when it starts. A `base` zone left from a
>   previous run would therefore be in memory from the first query — which the
>   published runs never had. Removing `zones.db` before launching keeps the
>   fixture identical; `seed_memory.py` recreates `base` in SQLite only.
>
> Or run in a scratch workspace, which is how the 2026-09-16 numbers were
> produced: `export ROBOT_WS=/tmp/bench` (with `data/knowledge` copied in) for
> both the launch and the eval scripts — your own memory stays untouched.
>
> Skipping this is what makes the `zone_nav` control appear to fail — a
> session-state artefact, not a property of RAG.

## 3. Run the benchmark suites

```bash
python3 run_benchmark.py tasks_full.yaml        # 63 runs (~6 min): ablation + attribute + controls
python3 run_benchmark.py tasks_phrasing.yaml    # 54 runs (~5 min): phrasing/language robustness
```

Each runs three conditions and writes `results/<suite>/{rag,norag,rag_unchecked}.json`
(per-run records with the decision, the plan as it would run — with
`raw_steps` and `plan_corrections` when the check replaced a step — and
goal→plan latency):

| Condition | `rag_enabled` | `plan_validation` | Measures |
|---|---|---|---|
| `rag` | true | true | the system as shipped |
| `norag` | false | true | what RAG adds (the headline ablation) |
| `rag_unchecked` | true | false | what the plan check adds ([ADR-032](decisions/ADR-032-plan-check-before-execution.md)) |

The script flips the parameters live with `ros2 param set` — no restarts, same
memory state in every condition. It **reads each value back and retries**; if
you see `WARNING: could not confirm ...`, DDS discovery has not settled (common
right after launch) — the run would be invalid, so wait a few seconds after
`rag_node ready` before starting, and re-run.

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
python3 run_benchmark.py tasks_hard.yaml     # 90 runs (~9 min)
python3 report.py hard
```

> **Why opt-in.** The distractors add competing entries to semantic memory,
> which changes retrieval for *every* query. Seeding them by default would
> silently invalidate the committed `tasks_full` results, which were measured
> against a three-landmark memory. Run plain `seed_memory.py` to reproduce the
> published numbers; add `--hard` only for this suite.

Measured result (2026-09-16): **23/30 with RAG and the plan check**, 19/30 with
the check off, 6/30 without RAG. By type, with RAG and the check: disambiguation
9/9, ordered 7/9, spatial relation 1/6, plausible nonexistent place 6/6 (0/6
with the check off) — see [rag-analysis.md §2.6 and §2.9](rag-analysis.md). The
July run scored 27/30 with a different knowledge base and no check (§2.8). The per-run
`decision` label is richer than pass/fail — `visited_both`, `out_of_order`,
`incomplete_2_of_3`, `wrong_landmark`, `hallucinated` — so a failure says *how*
the planner was wrong. `plot_results.py` renders that breakdown as `results/hard_decisions.png`.

The scoring rules live in `eval/scoring.py` (pure logic, no ROS) and are
covered by `tests/test_benchmark_scoring.py`, so the suite's definition of
success is verified without needing a simulator (ADR-018).

### 3c. RAG vs LLM → SQL on the same places (ADR-033)

The ablation above compares memory with no memory. This experiment compares two
ways of *looking up* the same memory: vector similarity (`rag`) and a SELECT
the planner's model writes over a SQLite copy of the same places (`sql`).
Everything else is identical — prompt, knowledge base retrieval, zone table,
plan check, scoring — and all conditions run in one session on one memory.
Results go to their own directory, so the published ones are untouched.

```bash
# after exploring and seeding (§1–2); the export must follow each seeding
python3 export_places_sql.py                                      # places.db = semantic_map
python3 run_benchmark.py tasks_full.yaml --conditions rag,sql,norag --out sql-experiment
python3 run_benchmark.py tasks_phrasing.yaml --conditions rag,sql,norag --out sql-experiment
python3 seed_memory.py --hard --reset-zones && python3 export_places_sql.py
python3 run_benchmark.py tasks_hard.yaml --conditions rag,sql,norag --out sql-experiment
python3 report.py hard --root results/sql-experiment              # and full, phrasing
```

Each `sql` run stores the query the model wrote, how many rows it returned and
any error in the plan's `memory_query`, so every miss can be traced to the
query or to the planner.

Measured result (2026-09-17, `eval/results/sql-experiment-2026-09-17/`):
main suite 21/21 · 21/21 · 6/21 (rag · sql · norag), phrasing 18/18 · 18/18 ·
1/18, hard 27/30 · 24/30 · 6/30 — the difference is the spatial relations, 3/6
vs 0/6. Median latency 2.4 · 3.0 · 1.4 s. Analysis in
[rag-analysis §2.10](rag-analysis.md).

## 4. Embedding model comparison (no ROS needed)

```bash
python3 embedding_bench.py        # nomic-embed-text vs bge-m3, ES vs EN
```

Writes `results/embeddings.json` and prints the accuracy/margin table. Run it
*after* the LLM suites so the embedding calls don't pollute latency numbers.

## 4b. Zone descriptions: name vs function (no ROS needed)

```bash
python3 room_semantics_bench.py   # "ve donde se suele cocinar" vs a named zone
```

Embeds five named zones described both ways — the old name-plus-bounds text and
the current one carrying what the room is for — and queries them with 11
functional goals. Writes `results/room_semantics.json` and prints top-1, how
many queries clear the planner's 0.40 threshold, and every query the old
descriptions sent to the wrong room. Expected: 55% → 100% top-1
([ADR-022](decisions/ADR-022-room-semantics.md), analysed in
[rag-analysis §2.7](rag-analysis.md)). Needs only bge-m3 in Ollama.

## 4c. Exploration strategies (needs the simulator)

The numbers in [ADR-027](decisions/ADR-027-exploration-frontier-clusters.md)
(nearest 9.1 m² vs the adopted clusters 14.1 m² in 240 s) come from this procedure,
**one fresh launch per strategy** — the SLAM map must start empty each time:

```bash
ros2 launch robot_bringup full_system.launch.py use_rviz:=false     # terminal 1
ros2 param set /skills_executor_node explore_strategy nearest       # or: clusters
python3 exploration_coverage.py                                     # before
ros2 service call /skills/execute robot_interfaces/srv/ExecuteSkill \
  "{skill_name: 'explore', params_json: '{\"duration_sec\": 240}'}"
python3 exploration_coverage.py                                     # after
```

**Rebuild before measuring.** In this workspace `colcon build --symlink-install`
installs copies of Python modules, so an edit is not live until rebuilt — one
published run was invalidated exactly that way (ADR-027).

`exploration_coverage.py` reads the map the dashboard renders and counts every
cell that is not unexplored. Planning failures are counted from the launch log:
`grep -c "failed to plan" <log>`. These are single runs; the ADR says so.

## 5. Tables and charts

```bash
python3 report.py full            # markdown table -> results/full/report.md
python3 report.py phrasing
python3 plot_results.py           # results/{benchmark,phrasing,embeddings}.png
                                  # + room_semantics.png, and hard{,_decisions}.png
                                  #   and plan_check.png when their results exist
```

The PNGs are the ones embedded in the README and docs/rag-analysis.md.
`report.py hard` works the same way.

## 5b. When the numbers must be re-measured

The planning suites measure *this code with these knowledge files*. Re-run all
three (full, phrasing, hard) before quoting a number again after any change to:

- `data/knowledge/` — every chunk can land in the prompt. A single worked
  example once cost the impossible-goal control 3/3 → 0/3
  ([ADR-031](decisions/ADR-031-knowledge-base-is-planner-input.md)).
  `rag_node` re-syncs the collection when the files change, so no wipe is
  needed.
- the planner prompt, the plan check or plan parsing (`robot_brain/prompts.py`,
  `plan_validation.py`, `plan_parsing.py`), the retrieval parameters (`top_k`,
  `rag_score_threshold`), or the embedder.
- how memories are written (`robot_rag`), or what `seed_memory.py` seeds.

When a re-run replaces published results, keep the old ones, each with its own
`report.md` per suite:

- `eval/results/before-kb-fix-2026-09-16/` — the run that found the knowledge
  base regression (ADR-031);
- `eval/results/zone-repair-experiment-2026-09-17/` — a variant of the plan
  check that was measured and reverted (ADR-032). Its `landmarks.json` is the
  layout it was scored against.

**Compare conditions inside one session, not totals across sessions.** Two
sessions of identical code differed by two runs on the hard suite and by three
on one task type (rag-analysis §2.9). Do not iterate knowledge
wording against the suites until they pass. Diagnose with the failing goals,
fix the cause, measure once, and publish what comes out.

## 6. Tests and lint

Pure logic — no ROS needed (includes the benchmark scorer):

```bash
cd ~/robot_ws
source agent_env/bin/activate
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/    # 408 tests
ruff check .
```

Node level — needs a sourced workspace:

```bash
source ~/robot_ws/setup_env.sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 colcon test && colcon test-result --all   # 28 tests
```

(`PYTEST_DISABLE_PLUGIN_AUTOLOAD` avoids the ROS-installed `launch_testing`
pytest plugin, which is incompatible with current pytest and breaks collection
outright. The no-ROS CI job doesn't need it; the ROS job sets it. See ADR-018.)

## Changing conditions manually

```bash
ros2 param set /llm_planner_node rag_enabled false     # ablate RAG
ros2 param set /llm_planner_node plan_validation false # ablate the plan check
ros2 param set /llm_planner_node dry_run true          # plan without driving
ros2 param set /llm_planner_node zones_in_prompt false # hide the known zones too
```

All four are re-read per goal — no restart needed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No map received` in seed_memory | Sim not running or SLAM not up — use Option A |
| Plans never arrive (`no_plan`) | Check Ollama: `ollama ps`; first call after idle takes ~4 s extra (model load). If the planner log says `Failed to parse plan`, Qwen returned JSON with a `// comment` in it — seen 3 times without RAG in one run; the goal gets no plan and scores `no_plan` |
| `Unknown zone: base` in the control task | Re-run `seed_memory.py` (zone lives in `data/zones.db`) |
| Landmarks empty after relaunch | `data/chroma_db` was deleted — re-run seeding |
| Code, launch or config changes not taking effect | This workspace installs copies, not links: rebuild the package (`rm -rf build/<pkg> install/<pkg>` first if in doubt) |
| Live map grows a second, rotated copy of the house | Two simulations are running: a closed terminal left a `gz sim` server behind. Current builds refuse to launch next to one (ADR-034); otherwise Ctrl+C, `kill` the old server's PID, relaunch. The saved map on disk is not affected |
| The robot wedges against a wall, then nothing localizes | Wheel slip while pushing corrupts odometry (up to 99° measured) and SLAM with it. Check `robot_radius: 0.20` in `nav2_params.yaml` (ADR-036) and restart the run; a map recorded through a slip has to be mapped again |
| Every goal fails to plan and the robot never moves | The run starts where the map begins, a nook 0.25 m from a wall. Launch with `start_zone:=<a named zone>` (ADR-035); `demo.launch.py` already does |
| Everything moves at half speed | Check the dashboard's real-time factor: 0.90 is the measured figure with the camera at 5 Hz and no voxel layers (ADR-037), 0.47 without them. Close RViz (`use_rviz:=false`) for more |
| Process aborts at script exit | Fixed via `SpinHandle.stop()`; if it reappears, results are already written before teardown |
