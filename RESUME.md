# Project Review — Robot RAG Agent

**Review date:** 2026-07-20
**Scope:** full pre-publication review — code, docs, evaluation, dashboard,
packaging, CI readiness.
**Verdict:** published and green. Remaining work is listed in §8.

This document is the honest state of the project: what was verified by
actually running it, what was broken and got fixed, what the technical
decisions are and why, and what still does not work. It is deliberately not a
sales pitch — the weaknesses are listed with the same weight as the strengths.

---

## 1. What the project is

A cognitive agent for a simulated mobile robot. It takes a natural-language
goal, retrieves context from a semantic memory (RAG over ChromaDB), plans with
a local LLM (Qwen2.5-7B via Ollama), executes the plan through ROS 2 skills,
and reports back on what actually happened.

Seven ROS 2 packages, ~5,000 lines of Python, 18 ADRs, 126 automated tests, a
measured ablation benchmark, and a web dashboard.

The distinguishing claim is not "I built a RAG robot" — it is **"I measured
whether the RAG helps, and published the conditions under which it does not."**
That is what §6 protects and what a reviewer will actually judge.

---

## 2. Verification status

Everything below was executed during this review, not inferred.

| Check | Command | Result |
|---|---|---|
| Pure-logic tests | `pytest tests/` | **106 passed** (was 52) |
| Node-level tests | `colcon test` | **20 passed** (was 10 failures — §3.3) |
| Python lint | `ruff check .` | **clean** |
| Workspace build | `colcon build --symlink-install` (from clean) | **7/7 packages** |
| Dashboard HTTP API | live node, every endpoint exercised with `curl` | **all correct** |
| Clean shutdown | `kill -INT <pid>` | **silent exit** (was 2 tracebacks — §3.2) |
| Path portability | ran with `ROBOT_WS` pointed at a scratch dir | **data written there** |
| Charts | regenerated from committed result JSON | **reproduce exactly** |
| Committed artefacts | `git ls-files` vs `.gitignore` | **no build/venv/DB leakage** |
| Fresh clone | `git clone` + build + `colcon test` in a `ros:jazzy-ros-base` container | **20/20, no reference to the original home dir** |
| GitHub Actions | both jobs on the pushed commit | **green** |

Not verified: end-to-end robot behaviour in Gazebo and the hard benchmark
suite — both need the full sim stack plus Ollama (§6.2, §6.3).

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
ordinary ROS 2 parameters. → **[ADR-015](docs/decisions/ADR-015-workspace-relative-paths.md)**

### 3.2 Every node crashed on shutdown — *high*

All four `main()` functions caught only `KeyboardInterrupt`. Under `ros2 launch`,
shutdown arrives as `ExternalShutdownException`, which escaped — and then the
`finally` block called `rclpy.shutdown()` on an already-torn-down context,
raising `RCLError` on top. Two tracebacks per node, eight per stack stop.

This was not cosmetic: all four nodes run with `respawn=True`, so a traceback on
exit is indistinguishable in the log from the genuine crash respawn exists to
recover from. It made the launch stack read as flaky and buried real errors.

**Fixed:** both exceptions caught, `rclpy.shutdown()` guarded by `rclpy.ok()`,
applied uniformly. → **[ADR-016](docs/decisions/ADR-016-node-shutdown-contract.md)**

### 3.3 `colcon test` failed in all five Python packages — *medium*

The stock `ros2 pkg create` lint tests (`test_copyright`, `test_flake8`,
`test_pep257`, identical boilerplate in all six packages) contradicted the
project's own conventions: `I100` disagreed with the documented import order
that `ruff` enforces, and `D401` demanded imperative docstrings against the
codebase's consistent third-person Google style. Ten failures, permanent.

**Fixed:** boilerplate removed, `ruff` confirmed as the single Python linter.
`robot_interfaces` keeps `ament_lint_auto` because it checks a different
artefact class (manifest XML, CMake) — and it immediately earned its keep by
catching a real schema violation (§3.4). → **[ADR-017](docs/decisions/ADR-017-single-linter-ruff.md)**

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

- README claimed **47** tests; there were **52** (now 106 + 20).
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

**Fixed:** a three-layer strategy (→ **[ADR-018](docs/decisions/ADR-018-test-strategy.md)**).

- **Layer 1, 106 tests, no ROS.** Two modules were restructured to join it:
  `robot_dashboard/web_api.py` (the FastAPI app split out of the node, built
  against a node *interface* so a stub can drive it — this also finally uses the
  `httpx` dependency that was sitting unused) and `eval/scoring.py` (the
  benchmark scorer, split out of `run_benchmark.py`).
- **Layer 2, 20 tests, needs ROS.** Real nodes on real executors: service
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
have headroom, with strict scoring in `eval/scoring.py` and 26 unit tests
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

## 4. Technical decisions

The full reasoning lives in `docs/decisions/`. Condensed:

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
Previously the nodes had no automated coverage at all. Now 106 pure-logic +
20 node-level tests. What remains uncovered is layer 3 — anything needing
Gazebo, Nav2 or Ollama — which is documented as manual rather than claimed.

### 6.2 End-to-end navigation is unreliable
The robot wedges in narrow doorways and `navigate` occasionally reports false
success, on software-rendered physics. This is why the benchmark measures
planning (ADR-013) — a legitimate choice, *provided* it stays clearly labelled,
which it currently is. Physical SR/SPL numbers do not exist.

### 6.3 The headline benchmark is saturated — *addressed, but unrun*
21 runs per condition, split 9/6/3/3 across task types, every cell at 100% or
0%. Two consequences:
- **The control cells are thin.** 3/3 is weak evidence; the `n=` labels now make
  that visible instead of hiding it.
- **No headroom.** It cannot show improvement from here, which makes it a poor
  instrument for the roadmap's agent loop.

`eval/tasks_hard.yaml` now exists to fix the second point — 10 tasks across four
new types, designed to be failable by the current system (§3.8). **It has not
been run**: that needs the full sim stack plus Ollama. No numbers are claimed
for it anywhere, and `plot_results.py` refuses to draw a chart until real
results exist. Running it is the first item in §8.

### 6.4 Perception cannot name objects
Colors and clutter only (ADR-014). "Go to the white, open room" works; "go to
the room with the chair" does not. Documented.

### 6.5 Dashboard: threading and exposure
Two latent issues, neither observed to fail but both real:
- `dashboard_node` runs on a single-threaded `rclpy.spin` while uvicorn threads
  call node methods (`call_async`, `wait_for_service`, TF lookups). `rclpy` does
  not guarantee thread safety here. The other nodes follow ADR-007 discipline;
  this one predates it. **A `MultiThreadedExecutor` with the client in its own
  callback group would bring it in line** — recommended, not yet done.
- `POST /api/zones` blocks for ~2 s when `rag_node` is down (measured), because
  it waits on the service inside the request handler. Should be fire-and-forget.
- The server binds `0.0.0.0` with **no authentication**. Fine on WSL2 loopback,
  wrong the moment it is on a shared network. Consider defaulting to `127.0.0.1`.

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
- `httpx` is in `requirements.txt` for the FastAPI `TestClient`, but no
  dashboard tests exist. Either write them (the API is small and very testable)
  or drop the dependency.
- `eval/landmarks.json` is gitignored — correctly, since it is tied to one SLAM
  map, but it means the benchmark cannot be replayed from a clone without
  re-seeding. `docs/EVALUATION.md` covers the procedure.

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

**Still to do:**
1. **Run the hard suite** (`seed_memory.py --hard`, then
   `run_benchmark.py tasks_hard.yaml`). Needs Gazebo + Ollama. Until then it is
   scaffolding with verified scoring and no data — stated as such everywhere it
   appears, and it should stay that way until it has really run. Do this
   *before* the agent loop: a baseline taken afterwards is worth much less.
2. **Launch the full stack from a clean clone once.** Build and tests are
   confirmed; `ros2 launch robot_bringup full_system.launch.py` on a fresh
   checkout, with Gazebo actually coming up, is the part no container can prove.

**Recommended, in priority order:**
3. Bring `dashboard_node` onto a `MultiThreadedExecutor` (§6.5) — the only
   place the codebase contradicts its own ADR-007.
4. Default `http_host` to `127.0.0.1`.
5. Make `POST /api/zones` fire-and-forget.

**Deliberately not done**, listed so the decisions are visible: the
`src/robot_bringup/config/install/` directory — a stray colcon artefact sitting
inside the source tree. It is gitignored so it will not reach GitHub, and I did
not create it, so I left it for you to delete.

---

## 9. Roadmap (unchanged, still the right order)

1. **Real agent loop** — replanning from execution feedback. The jump from
   plan-then-execute to a true agent, and the natural next benchmark section.
   Note §6.3: it needs a harder benchmark to show any gain.
2. **Native voice** — Whisper on the Windows NPU (unreachable from WSL2).
3. **Object-level detection** — YOLOv8n; revisit a VLM on real-camera hardware.
4. **SLAM map save/load** — prerequisite for a reproducible physical SR/SPL run.
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
nobody had run, a stale test count. None of them touched the architecture, which
held up under review.

The two substantive weaknesses named in the first pass have now been addressed
rather than merely documented: node-level coverage went from zero to 20 tests
(§3.7), and the saturated benchmark gained a suite with actual headroom (§3.8).
Both fixes paid for themselves immediately — making the nodes testable exposed
two further bugs (the `navigate` validation ordering, the `package.xml` schema
violation), which is the usual return on that kind of work.

What is left is honest and worth saying out loud: **the hard suite has no
numbers yet**, and until it is run the claim "the benchmark has headroom" is a
design argument rather than a measured one. Run it before the roadmap's agent
loop, not after — a baseline taken afterwards is worth much less.
