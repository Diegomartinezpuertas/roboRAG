# ADR-018: Three-layer test strategy, and testing the nodes

**Date:** 2026-07-20
**Status:** Accepted

## Context

After [ADR-017](ADR-017-single-linter-ruff.md) removed the boilerplate lint
tests, the honest position was stated in the README: **the ROS nodes had no
automated coverage at all.** The 52-test suite in `tests/` covered pure logic —
chunking, plan parsing, prompt building, frontier selection, the scene
descriptor, the zone store — and stopped at the point where anything touched
`rclpy`. Everything that made this a robot rather than a library was verified by
hand: service wiring, callback groups, the HTTP↔ROS bridge, shutdown.

That gap had already cost something real. The shutdown defect fixed in
[ADR-016](ADR-016-node-shutdown-contract.md) — every node printing two
tracebacks on an ordinary `ros2 launch` stop — survived the entire life of the
project because nothing ever exercised a node's exit path.

The obstacle was environmental, not conceptual. Node tests need a ROS
installation, which the CI job did not have and which cannot be `pip install`ed;
and some nodes need more than ROS (`rag_node` ingests its knowledge base
against a live Ollama server on startup).

## Decision

Three layers, each defined by what it needs to run.

**Layer 1 — pure logic (`tests/`, no ROS).** Runs on any machine and in the
existing CI job. Now 106 tests. Two modules were deliberately restructured to
join this layer rather than sit above it:

- `robot_dashboard/web_api.py` — the FastAPI app, request models and map
  rendering, split out of `dashboard_node.py`. It is built against a *node
  interface* (a handful of methods), not against `DashboardNode`, so the entire
  HTTP surface is testable with a stub. The `httpx` dependency, previously
  present for a `TestClient` nobody used, now earns its place.
- `eval/scoring.py` — the benchmark plan scorer, split out of
  `run_benchmark.py`. The scorer decides what the README's headline numbers
  mean, and the hard suite's ordered-subsequence matching and derived
  relational ground truth are easy to get subtly wrong; without this split they
  would only ever be exercised by a full run against a live simulator and LLM.

**Layer 2 — node level (`src/<pkg>/test/`, needs ROS).** Run by `colcon test`
and by a new CI job in a `ros:jazzy-ros-base` container. 20 tests covering what
a stub cannot:

- `robot_skills` — the real node on a real executor, `/skills/execute` called
  over a real service client: the service is advertised, types round-trip,
  unknown skills and malformed JSON produce well-formed error responses, and a
  failed call does not poison the executor.
- `robot_dashboard` — the real node with its real uvicorn server on a throwaway
  port: a goal POSTed over HTTP arrives on `/robot/goal` as a `std_msgs/String`,
  rejected goals are never published, messages on the robot's topics reach the
  event stream, event ids stay monotonic so the UI's incremental polling works.
- `robot_bringup` — the shutdown contract, one parametrised test per node:
  spawn the installed executable, SIGINT, assert a zero exit and no traceback.

**Layer 3 — manual.** Anything needing Gazebo, Nav2 or Ollama: end-to-end
navigation, exploration, perception, the planner's LLM calls. Documented as
manual in the README rather than faked.

## Rationale

**Why restructure code to make it testable, rather than test around it?** The
dashboard's HTTP layer had no business importing `rclpy` — it never used it.
The split is better design independently of testing; the coverage is what
revealed that. The same holds for the scorer, which is arithmetic over
dictionaries.

**Why in-process nodes for layer 2 instead of `launch_testing`?**
`launch_testing`'s pytest plugin, as shipped with Jazzy, is incompatible with
current pytest and breaks collection outright — `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`
is required to run `colcon test` at all. Instantiating the node and spinning it
on a `MultiThreadedExecutor` in a thread is simpler, faster, more deterministic,
and exercises the same middleware path. The one case that genuinely needs a
separate process — the shutdown contract — spawns one directly with
`subprocess`.

**Why spawn the installed executable rather than `ros2 run` for the shutdown
test?** `ros2 run` does not forward a programmatic SIGINT to its child (verified:
the node kept running), which would have made the test vacuous. `ros2 launch`
executes the installed executable directly, so that is what gets tested.

**Why `rag_node` is excluded from layer 2.** It ingests the knowledge base on
startup, which requires a live Ollama server. Adding one to CI means a model
download per run for a node that follows the identical `main()` contract as its
three siblings. Documented rather than papered over.

**Verification that the tests have teeth.** The shutdown fix was temporarily
reverted and the suite re-run; the `dashboard_node` case failed with exactly the
original `RCLError: rcl_shutdown already called`. A test that has never been
seen to fail is not yet evidence of anything.

## Consequences

- Coverage goes from 52 pure-logic tests to **106 pure-logic + 20 node-level**.
  The claim "the nodes have no automated coverage" is no longer true and has
  been removed from the README.
- `colcon test` requires `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and a sourced
  workspace. Documented in the README, `CLAUDE.md` and the CI job.
- CI grows a second job on a ROS container. It is slower (an apt install per
  run) but it guards the layer where the real defects have historically been.
- One real defect was found while writing these tests: `navigate` blocked on the
  Nav2 lifecycle *before* validating its parameters, so an unknown zone name
  hung indefinitely instead of returning "Unknown zone: X. Known zones: [...]".
  Validation now happens first. This is the second bug found by the act of
  making something testable, after the `package.xml` schema violation in
  ADR-017 — the pattern is worth noting.
- Node tests write to a temporary `ROBOT_WS` (per-package `conftest.py`), so
  running the suite never touches the developer's real `zones.db` or logs.
