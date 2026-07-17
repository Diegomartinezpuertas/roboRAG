# ADR-013: RAG benchmark at the planning level (not end-to-end)

**Date:** 2026-07-16
**Status:** Accepted

## Context

The project's hypothesis is "RAG improves natural-language navigation". The
original plan measured end-to-end SR/SPL: drive the robot to each landmark,
seed the reached pose, run tasks, and measure whether the robot arrives.

Attempting it collided with the reality of a WSL2, software-rendered sim:

- The robot **wedges** against walls/narrow doorways of `turtlebot3_house`,
  and `collision_monitor` + the Spin recovery don't free it (every reached
  pose collapsed to the same point).
- The `navigate` skill sometimes **reports success while the robot is
  elsewhere**, and narrow doorways make point-to-point navigation unreliable.

With that instability, end-to-end SR/SPL doesn't produce statistically
meaningful numbers without many hours of sim-wrangling and better hardware.

## Decision

Measure the hypothesis at its **causal mechanism**, the planning decision,
which is reproducible and independent of low-level navigation:

> Does the RAG-equipped planner navigate DIRECTLY to the remembered
> landmark's coordinates, versus falling back to blind EXPLORE without RAG?

Implementation:
- `dry_run` mode in `llm_planner_node`: produce and publish the plan but skip
  execution (don't drive the robot).
- `/robot/plan` topic with each goal's raw plan JSON.
- `eval/run_benchmark.py` iterates conditions × tasks × repetitions, captures
  the plan and classifies it: `direct_nav` (a navigate step within ±0.75 m of
  the landmark), `explore`, or `other`. Task types add controls: `zone_nav`
  (resolvable without RAG — should pass in both conditions) and `negative`
  (nonexistent place — success is *not* inventing coordinates).
- `temperature=0` → reproducible; RAG retrieval (real calls through
  `/rag/query`) is the only variable between conditions.

Landmarks are seeded directly into `semantic_map` at distinct free-space
coordinates (not by driving), because measuring the decision only requires
plausible, distinct coordinates.

## Consequences

- The headline result is defensible and reproducible: it quantifies that RAG
  changes the navigation decision (direct vs explore). It is the hypothesis'
  mechanism, not a distant proxy.
- End-to-end execution (SR/SPL) remains a qualitative demo and **future
  work** on better hardware / a world with friendlier chokepoints. The
  end-to-end harness (reset-to-home, odometry integration, SPL) exists in
  `run_benchmark.py`'s git history, ready to reactivate.
- Honesty requirement: the README must present this as "measured at the
  planning level" and explain why — not sell it as physical SR.
