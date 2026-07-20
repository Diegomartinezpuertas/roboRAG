# Engineering review — Robot RAG Agent

**Date:** 2026-07-20
**Scope:** full pre-publication review — code, docs, evaluation, dashboard,
packaging, CI.

An internal engineering record, not a summary of the project: for what the
project *is*, start at the [README](../README.md); for the reasoning behind
individual decisions, see [decisions/](decisions/).

What this document is for is the audit trail — what was verified by actually
running it, what was broken and how it was fixed, and what still does not
work. The weaknesses are listed with the same weight as the strengths, which
is the only way a document like this stays useful.

---

## 1. What the project is

A cognitive agent for a simulated mobile robot. It takes a natural-language
goal, retrieves context from a semantic memory (RAG over ChromaDB), plans with
a local LLM (Qwen2.5-7B via Ollama), executes the plan through ROS 2 skills,
and reports back on what actually happened.

Seven ROS 2 packages, ~5,000 lines of Python, 19 ADRs, 141 automated tests, a
measured ablation benchmark, and a web dashboard.

The distinguishing claim is not "I built a RAG robot" — it is **"I measured
whether the RAG helps, and published the conditions under which it does not."**
That is what §6 protects and what a reviewer will actually judge.

---

## 2. Verification status

Everything below was executed during this review, not inferred.

| Check | Command | Result |
|---|---|---|
| Pure-logic tests | `pytest tests/` | **120 passed** (was 52) |
| Node-level tests | `colcon test` | **21 passed** (was 10 failures — §3.3) |
| Python lint | `ruff check .` | **clean** |
| Workspace build | `colcon build --symlink-install` (from clean) | **7/7 packages** |
| Dashboard HTTP API | live node, every endpoint exercised with `curl` | **all correct** |
| Clean shutdown | `kill -INT <pid>` | **silent exit** (was 2 tracebacks — §3.2) |
| Path portability | ran with `ROBOT_WS` pointed at a scratch dir | **data written there** |
| Full stack, end to end | `full_system.launch.py` + explore + seed + all 3 suites, live | **runs; numbers reproduce** |
| Headline ablation | `tasks_full.yaml`, live | **9/9·6/6·3/3·3/3 with RAG; 0·0·3/3·3/3 without** |
| Hard suite | `tasks_hard.yaml`, live | **27/30 with RAG vs 3/30 without** |
| Charts | regenerated from live result JSON | **reproduce** |
| Committed artefacts | `git ls-files` vs `.gitignore` | **no build/venv/DB leakage** |
| Fresh clone | `git clone` + build + `colcon test` in a `ros:jazzy-ros-base` container | **20/20, no reference to the original home dir** |
| GitHub Actions | both jobs on the pushed commit | **green** |

Not verified: physical navigation success (SR/SPL) — the benchmark is at the
planning level by design (§6.2).

---

## 3. Defects found and fixed

### 3.1 Hardcoded absolute paths — *blocker*

Fifteen occurrences of `/home/diego/robot_ws/...` across four node defaults,
`agent_params.yaml`, both `eval/` scripts, and `setup_env.sh`. A fresh clone by
anyone else would write its database to a nonexistent path or fail outright.
This also violated the project's own stated rule ("No hardcoded paths").

**Fixed:** `setup_env.sh` now resolves its own directory and exports `ROBOT_WS`;
every node derives its defaults from it, falling back to `~/robot_ws`. Path
keys were removed from `agent_params.yaml` entirely (they were a second,
drift-prone definition site — and which one won depended on whether the node
was started via `ros2 launch` or `ros2 run`). Paths remain overridable as
ordinary ROS 2 parameters. → **[ADR-015](decisions/ADR-015-workspace-relative-paths.md)**

### 3.2 Every node crashed on shutdown — *high*

All four `main()` functions caught only `KeyboardInterrupt`. Under `ros2 launch`,
shutdown arrives as `ExternalShutdownException`, which escaped — and then the
`finally` block called `rclpy.shutdown()` on an already-torn-down context,
raising `RCLError` on top. Two tracebacks per node, eight per stack stop.

This was not cosmetic: all four nodes run with `respawn=True`, so a traceback on
exit is indistinguishable in the log from the genuine crash respawn exists to
recover from. It made the launch stack read as flaky and buried real errors.

**Fixed:** both exceptions caught, `rclpy.shutdown()` guarded by `rclpy.ok()`,
applied uniformly. → **[ADR-016](decisions/ADR-016-node-shutdown-contract.md)**

### 3.3 `colcon test` failed in all five Python packages — *medium*

The stock `ros2 pkg create` lint tests (`test_copyright`, `test_flake8`,
`test_pep257`, identical boilerplate in all six packages) contradicted the
project's own conventions: `I100` disagreed with the documented import order
that `ruff` enforces, and `D401` demanded imperative docstrings against the
codebase's consistent third-person Google style. Ten failures, permanent.

**Fixed:** boilerplate removed, `ruff` confirmed as the single Python linter.
`robot_interfaces` keeps `ament_lint_auto` because it checks a different
artefact class (manifest XML, CMake) — and it immediately earned its keep by
catching a real schema violation (§3.4). → **[ADR-017](decisions/ADR-017-single-linter-ruff.md)**

### 3.4 `robot_interfaces/package.xml` did not validate — *low*

`<member_of_group>` appeared before the dependency blocks; package format 3
requires it last, immediately before `<export>`. **Fixed** and verified with
`ament_xmllint`.

### 3.5 Charts hid their sample size — *medium (credibility)*

The benchmark charts showed bare 100%/0% bars. A 100% built on n=3 and one
built on n=9 rendered identically, and an *empty* cell would have rendered as an
indistinguishable 0% — the plotting code returned `0.0` for a missing subset.
For a project whose whole argument rests on measurement, that is the weakest
possible presentation of its strongest asset.

**Fixed:** every bar group now carries `n=` in its tick label, which makes the
n=3 control cells visibly weaker than the n=9 headline result and exposes empty
cells as `n=0`. Also: the zero line in the margin chart now draws above the bars
(it was showing only in the gaps, reading as stray dashes), and the latency
strip dots no longer collide with the axis edge.

### 3.6 Stale documentation claims — *low, but corrosive*

- README claimed **47** tests; there were **52** (now 120 + 21).
- README claimed *"ROS nodes and wiring are exercised locally with `colcon test`"*.
  False twice over: the command was red, and those tests were stock linters
  that exercise no node and no wiring. The testing section now states plainly
  what each layer covers and what stays manual.
- `setup_env.sh` still referenced LangChain, removed back in ADR-012.
- ADR count was stale (14 → 18).

### 3.7 The nodes had no automated coverage — *high*

The pure-logic suite stopped at the point where anything touched `rclpy`, so
service wiring, callback groups, the HTTP↔ROS bridge and shutdown were verified
only by hand. That gap is what let §3.2 survive the project's entire life.

**Fixed:** a three-layer strategy (→ **[ADR-018](decisions/ADR-018-test-strategy.md)**).

- **Layer 1, 120 tests, no ROS.** Two modules were restructured to join it:
  `robot_dashboard/web_api.py` (the FastAPI app split out of the node, built
  against a node *interface* so a stub can drive it — this also finally uses the
  `httpx` dependency that was sitting unused) and `eval/scoring.py` (the
  benchmark scorer, split out of `run_benchmark.py`).
- **Layer 2, 21 tests, needs ROS.** Real nodes on real executors: service
  dispatch and error paths, the dashboard's HTTP→ROS bridge, and a
  parametrised shutdown-contract test that spawns each node's executable and
  SIGINTs it.
- **Layer 3, manual.** Gazebo/Nav2/Ollama behaviour — documented, not faked.

The shutdown test was validated by temporarily reverting the §3.2 fix and
confirming it failed with the original `RCLError`. A test never seen to fail is
not yet evidence.

**A third defect surfaced while writing these:** `navigate` blocked on the Nav2
lifecycle *before* validating its parameters, so an unknown zone name hung
indefinitely instead of returning `Unknown zone: X. Known zones: [...]`.
Validation now runs first. That is two real bugs found purely by making things
testable (after the `package.xml` schema violation in §3.4).

### 3.8 The benchmark could not measure progress — *medium*

Covered in §6.3. **Added:** `eval/tasks_hard.yaml` — four task types built to
have headroom, with strict scoring in `eval/scoring.py` and 31 unit tests
pinning down what counts as success:

| Type | What it probes | How it can fail |
|---|---|---|
| `distractor_nav` | disambiguating name-confusable memories (`estacion_a` vs `estacion_a_norte`) | wrong landmark, or hedging by visiting both |
| `ordered_multi_step` | holding a multi-step plan in order | dropped step, reordered, collapsed |
| `relational_nav` | reasoning over retrieved coordinates ("nearest to base") | wrong candidate, or hedging |
| `negative_plausible` | declining `estacion_d` when a/b/c exist | inventing coordinates or a zone name |

The per-run `decision` label says *how* it failed (`visited_both`,
`out_of_order`, `incomplete_2_of_3`…), which is what makes the suite useful for
guiding the next iteration rather than just scoring it.

Two deliberate choices worth knowing: the distractor landmarks are **opt-in**
(`seed_memory.py --hard`), because adding them changes retrieval for every query
and would silently invalidate the committed `tasks_full` results; and the
relational ground truth is **computed at scoring time** from the seeded
coordinates rather than written into the YAML, so it cannot drift out of sync
with the map.

### 3.9 Two bugs the benchmark could only find by actually running — *medium*

Running the full stack end-to-end (the thing §8 previously listed as *to do*)
turned up two reproducibility defects the offline tests could not:

- **Stale zones confounded the control.** A leftover zone named `zona_b` from an
  earlier session captured the control goal "Ve a la zona base", so `zone_nav`
  failed in *both* conditions for a reason unrelated to RAG. `seed_memory.py`
  now takes `--reset-zones` (delete all zones but the control before seeding)
  and warns when stray zones exist.
- **Stale task logs fed the planner coordinates from a dead map.** `task_history`
  had a log — "moved to the base at (-0.30, -1.06)" — from a *previous* SLAM
  map. Retrieval handed it to the planner as current, and it navigated to a
  coordinate that no longer meant anything, over the correct freshly-observed
  entry. `data/logs/` is not committed, so a fresh clone is clean; a dev machine
  is not. This is more than a benchmark nuisance: storing absolute coordinates
  in a memory that outlives the map is a latent **correctness** bug, now
  documented in [rag-pipeline.md §6](rag-pipeline.md); the real fix — map-session
  versioning — was then implemented (§3.10, ADR-019).

Neither was a code defect in the usual sense — both were session state leaking
into a measurement. The fix was partly procedural (EVALUATION.md now specifies
starting from clean session state, exactly what a fresh clone has) and partly a
scorer correction: `zone_nav` now counts `explore(zone=X)` as resolving the
zone, not only `navigate(zone=X)`, because both read the zone from SQLite and
send the robot there — which is precisely what the control is meant to test.
That correction is covered by five new scoring tests.

### 3.10 Follow-up fixes: dashboard threading, and the map-coordinate bug at the source

Three of the "recommended" items from the first pass, done and verified:

- **Dashboard on a `MultiThreadedExecutor`** (§6.5), the last place contradicting
  ADR-007. The change surfaced a shutdown segfault — uvicorn's daemon thread
  racing the rmw teardown — fixed by stopping the executor before destroying the
  node, and now covered by the shutdown-contract test over repeated SIGINTs.
- **`POST /api/zones` fire-and-forget**: the 2 s `wait_for_service` inside the
  HTTP handler became an instantaneous readiness check plus an un-awaited
  `call_async`. Measured 25 ms with RAG down (was ~2 s), with a regression test.
- **The stale-coordinate bug fixed at the source, not just the benchmark**
  (§3.9). §3.9 patched the *symptom* procedurally (start from clean state);
  this fixes the *cause*. Coordinate memories are now versioned by **map
  session** (ADR-019): every `semantic_map` / `task_history` write is tagged
  with the active map id, and retrieval of those collections is filtered to it,
  so a pose from a dead map is never returned. Verified live — a scene written
  under session A is retrieved under A and invisible under a rotated session B
  while ChromaDB still physically holds it. Paired with a `save_map` maintenance
  skill (SLAM Toolbox serialize) and a `map_session_id` reload parameter, so a
  saved map's memories come back on purpose. 9 new unit tests.

## 4. Technical decisions

The full reasoning lives in [decisions/](decisions/). Condensed:

| # | Decision | The real reason |
|---|---|---|
| 001 | ChromaDB as vector store | Embedded, persistent, no server to run alongside ROS |
| 002 | Qwen Robot Suite deferred | Weights never released; tracked, not blocking |
| 003 | venv bridged via `PYTHONPATH`, never activated | Activating swaps `python3` and breaks the `console_scripts` shebang colcon generates |
| 004 | SLAM Toolbox, no AMCL | Live mapping; the robot explores unknown space, so there is no prior map to localise against |
| 005 | FastAPI dashboard | Observability was the actual bottleneck when debugging the agent loop |
| 006 | CycloneDDS pinned to loopback | WSL2's multi-NIC setup (eth0/docker0) caused intermittent discovery failures |
| 007 | MultiThreadedExecutor + callback groups + `Event` waits | Nested spins and throwaway executors both silently steal entity ownership — two failed attempts documented |
| 008 | User-defined zones | Superseded by 011 |
| 009 | Camera dropped to 640×480 | A 1080p stream was silently dropping frames over DDS |
| 010 | Continuous canvas resize | Map rendering artefact in the dashboard |
| 011 | Zones in SQLite; RAG relevance threshold | Hardcoded fallback coordinates were fictional and sent the robot to the wrong place; an irrelevant top hit is *worse* than no context, because it enters the prompt as ground truth |
| 012 | LangChain removed; post-execution report | LangChain was a name→callable registry the LLM never tool-called. The report is now a **second** LLM call over real results, instead of the planner pre-writing the answer before anything ran |
| 013 | Benchmark at planning level | End-to-end nav is unreliable on this sim; the planning decision is the causal mechanism of the hypothesis *and* is reproducible at `temperature=0` |
| 014 | Classical scene descriptor, no VLM | The VLM was unreliable on software-rendered frames; colors + LIDAR clutter are honest about what they measure |
| 015 | `ROBOT_WS`-relative paths | §3.1 |
| 016 | Node shutdown contract | §3.2 |
| 017 | Single linter (ruff) | §3.3 |
| 018 | Three-layer test strategy | §3.7 — and the restructuring it forced (the dashboard's HTTP layer had no business importing rclpy) was better design independently of testing |
| 019 | Map-session versioning + SLAM persistence | §3.10 — coordinate memories are meaningless once the map they were logged against is gone; scope them to a map id |

**Architectural through-line:** every one of these is a decision to *remove* an
unreliable component rather than paper over it — LangChain, AMCL, the VLM, the
fallback coordinates, the second linter. ADR-012 and ADR-014 in particular
trade capability for honesty, and the README says so.

---

## 5. What works (verified)

- **The full cognitive loop.** Goal → RAG retrieval → Qwen plan → skill
  dispatch → grounded report. Skills: `navigate`, `explore`, `perceive`,
  `scan_360`, `report`.
- **Self-building memory.** Every place reached while exploring is described and
  stored with coordinates, so "go to the white, open room" resolves later
  without manual seeding. This is the most interesting property of the system
  and the benchmark's `attribute_nav` row measures it directly (6/6).
- **The dashboard.** Every endpoint verified live: page serves, event stream
  polls, SLAM map renders with robot pose and zones, goals publish to
  `/robot/goal`, zone create/delete works including coordinate normalisation
  (inverted drag rectangles are corrected). Empty goal → 400, unknown zone
  delete → 404. Degrades correctly when `/rag/update_map` is down: warns, saves
  the zone locally anyway.
- **Concurrency discipline.** The executor/callback-group design (ADR-007) is
  correct and, unusually, the *comments explain the failure modes that motivated
  it* — the most valuable code in the repo for a reviewer to read.
- **Reproducible evaluation.** Charts regenerate exactly from committed result
  JSON.

---

## 6. What does not work

### 6.1 Test coverage — *resolved, see §3.7*
Previously the nodes had no automated coverage at all. Now 120 pure-logic +
20 node-level tests. What remains uncovered is layer 3 — anything needing
Gazebo, Nav2 or Ollama — which is documented as manual rather than claimed.

### 6.2 End-to-end navigation is unreliable
The robot wedges in narrow doorways and `navigate` occasionally reports false
success, on software-rendered physics. This is why the benchmark measures
planning (ADR-013) — a legitimate choice, *provided* it stays clearly labelled,
which it currently is. Physical SR/SPL numbers do not exist.

### 6.3 The headline benchmark is saturated — *addressed and now run*
21 runs per condition, split 9/6/3/3 across task types, every cell at 100% or
0%. Two consequences:
- **The control cells are thin.** 3/3 is weak evidence; the `n=` labels now make
  that visible instead of hiding it.
- **No headroom.** It cannot show improvement from here, which makes it a poor
  instrument for the roadmap's agent loop.

`eval/tasks_hard.yaml` fixes the second point — 10 tasks across four new types,
built to be failable by the current system (§3.8). **It has now been run** on
the full stack: **27/30 with RAG vs 3/30 without.** It behaves exactly as
intended — leaves headroom and localises it. The planner solves disambiguation
(9/9) and ordered multi-step plans (9/9), but only 3/6 of the spatial-relation
tasks ("nearest to base"): it retrieves the right candidates and then fails the
distance comparison. That is now a *measured* target for the agent loop rather
than a guess. Full analysis in [rag-analysis.md §2.6](rag-analysis.md).

### 6.4 Perception cannot name objects
Colors and clutter only (ADR-014). "Go to the white, open room" works; "go to
the room with the chair" does not. Documented.

### 6.5 Dashboard: threading and exposure
- `dashboard_node` now runs on a `MultiThreadedExecutor` with the map-update
  client in its own callback group, in line with ADR-007 — it used to spin
  single-threaded while uvicorn threads called into it. **Fixed** (§3.10). The
  MTE change surfaced and fixed a shutdown segfault (uvicorn's daemon thread
  racing the rmw teardown): the executor is now stopped before the node is
  destroyed, verified over repeated SIGINTs by the shutdown-contract test.
- `POST /api/zones` blocked ~2 s when `rag_node` was down (a timed
  `wait_for_service` inside the request handler). Now fire-and-forget:
  instantaneous readiness check, `call_async` not awaited. **Fixed** (§3.10),
  measured at 25 ms with RAG down, with a regression test.
- **Still open:** the server binds `0.0.0.0` with no authentication. Fine on
  WSL2 loopback, wrong on a shared network. Out of scope for this pass;
  defaulting to `127.0.0.1` is the fix.

### 6.6 Minor
- `_render_map_png` is a pure-Python per-pixel loop plus dilation, re-run on
  every new map (~160k iterations for a 20 m map). Cached by timestamp, so not
  hot, but numpy would make it trivial.
- `/api/map` ships the full base64 PNG every 2 s whether or not it changed —
  no ETag or change check.
- Planner status strings mix English and Spanish (`Received goal:` alongside
  `Qwen razona:`, `Plan paso 1/2`). The dashboard UI is entirely Spanish while
  all documentation is English. Deliberate for the robot's *responses* (it
  replies in the user's language, by design), inconsistent for *internal status*.
- `eval/landmarks.json` is gitignored — correctly, since it is tied to one SLAM
  map, but it means the benchmark cannot be replayed from a clone without
  re-seeding. [EVALUATION.md](EVALUATION.md) covers the procedure.

---

## 7. What the evaluation does and does not prove

**Proves:** with semantic memory, the planner decides to navigate directly to
remembered locations; without it, it explores blindly instead. 9/9 vs 0/9 on
object-referenced goals, 6/6 vs 0/6 on description-referenced goals, at
`temperature=0`. Retrieval survives Spanish paraphrase and English rephrasing
(18/18). The embedding model is a genuine bottleneck: nomic-embed-text gets
Spanish→English top-1 right 43% of the time with a *negative* separation margin,
bge-m3 86% with a positive one.

**Does not prove:** that the robot physically reaches the goal more often (not
measured — §6.2); that RAG beats a plain SQL lookup at this scale (the
known-zone control shows it explicitly does **not** — 3/3 both ways); that the
result generalises beyond 21 runs on one map with one model.

The README already states all of this. **Keep it that way.** The control row
that shows RAG losing is the most credible thing in the repository — it is the
evidence that the benchmark was not built to flatter the conclusion.

---

## 8. Before publishing

**Done since the first pass:** the repository is published at
[Diegomartinezpuertas/roboRAG](https://github.com/Diegomartinezpuertas/roboRAG),
the CI badge points at the real workflow, and **both CI jobs are green**. The
ROS job needed two fixes, both found by reproducing it in the container locally
rather than by pushing repeatedly: `ros:jazzy-ros-base` ships no pip at all, and
`llm_planner_node` imports the `ollama` client library at module load. A fresh
`git clone` built and passed 20/20 inside that container, which is independent
confirmation of the ROBOT_WS portability fix.

**Also done in this pass — the full stack, end to end.** Launched
`full_system.launch.py` (Gazebo + Nav2 + SLAM + agent + dashboard), explored to
build a map, seeded, and ran all three benchmark suites live against Qwen and
bge-b3 on the GPU. Everything reproduces: the headline table matches the
published numbers, phrasing is 18/18 vs 0/18, and the hard suite ran for the
first time (§6.3). Two real issues surfaced and were fixed in the process
(§3.9), which is the entire reason for running it rather than trusting the
numbers already in the repo.

**Also done in this pass — three follow-ups (§3.10):** `dashboard_node` on a
`MultiThreadedExecutor` (the last ADR-007 hold-out, plus the shutdown segfault
that surfaced), `POST /api/zones` fire-and-forget, and the stale-coordinate bug
fixed at the source via map-session versioning (ADR-019). All verified live and
unit-tested.

**Deliberately left, out of scope this pass:**
- Default `http_host` to `127.0.0.1` (the no-auth `0.0.0.0` bind). A one-line
  change; skipped because it slightly changes access on the dev box and the
  user scoped it out.
- Physical SR/SPL — planning-level is the design (§6.2, ADR-013).
- The `src/robot_bringup/config/install/` directory — a stray colcon artefact in
  the source tree. Gitignored, so it will not reach GitHub, and not mine to
  delete; left for the author.

---

## 9. Roadmap

1. **Real agent loop** — replanning from execution feedback: the jump from
   plan-then-execute to a true agent. The hard suite (§6.3) already localises
   the payoff (spatial reasoning, 3/6) and gives the baseline to beat. Design
   written up in [agent-loop-roadmap.md](agent-loop-roadmap.md).
2. **Native voice** — Whisper on the Windows NPU (unreachable from WSL2).
3. **Object-level detection** — YOLOv8n; revisit a VLM on real-camera hardware.
4. **Full map save/load orchestration** — the pieces exist (serialize skill,
   `map_file_name`, `map_session_id`, ADR-019); a single `saved_map:=<id>`
   launch arg wiring SLAM + rag_node is the remaining convenience, and the
   prerequisite for a reproducible physical SR/SPL run.
5. **Docker/devcontainer** — kills the "works on my WSL2" caveat entirely.

---

## 10. Overall assessment

The engineering is solid and, more unusually, **honest**. The ADRs record real
failures and the reasoning that resolved them rather than reconstructing a
clean story after the fact; the README's "Honest limitations" section
volunteers weaknesses most portfolio projects hide; and the benchmark includes
a control condition that shows the project's central technology losing.

The defects found were real but almost all of a single kind: **the project was
never run as anyone other than its author**. Hardcoded home directories, a
shutdown path only exercised by Ctrl-C in a foreground terminal, a `colcon test`
nobody had run, a stale test count, session state (zones, task logs) leaking
into the benchmark. None of them touched the architecture, which held up under
review — every one was a boundary the author never had to cross because they
only ever ran it in place, on their own machine, with their own accumulated
state.

The two substantive weaknesses named in the first pass are now addressed and
verified, not merely documented: node-level coverage went from zero to 21 tests
(§3.7), and the saturated benchmark gained a suite with headroom that **has
been run** — 27/30 vs 3/30, localising the planner's weakness to spatial
reasoning (§3.8, §6.3). Every fix paid for itself by exposing another real
issue: making the nodes testable found two bugs (§3.7), and running the full
stack found two more (§3.9). That is the whole argument for doing this work
rather than trusting the numbers already checked in.

What remains is genuine and stated plainly throughout: the planning-level
benchmark is not physical SR/SPL (§6.2), spatial reasoning is measured but not
yet improved (the agent-loop target, §6.3), and perception is attribute-level
(§6.4). None of these are hidden; they are the honest edges of a project whose
defining trait is that it says where its own limits are.
