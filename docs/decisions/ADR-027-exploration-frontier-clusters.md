# ADR-027: Explore toward the largest frontier with clearance, not the nearest cell

**Date:** 2026-09-16
**Status:** Accepted

## Context

`explore` picked the **nearest frontier cell** — the closest free cell touching
unknown space. On the TurtleBot3 house that rule creeps: the nearest frontier is
almost always the next cell along the wall the robot is already beside, so it
visits many frontiers and opens few rooms. Five minutes of it mapped 9.7 × 4.4 m
(ADR-026).

While diagnosing that, a planning failure at a doorway was first attributed to
Nav2's `inflation_radius: 0.5` — "a 0.8 m door has no cost-free path" — and that
claim was written into ADR-026, the architecture notes and CLAUDE.md. **It was
wrong.** Measured afterwards, the robot crosses that doorway (≈0.7 m) at 0.5 m
inflation in both directions, in 28 s and 31 s. What had failed was a *goal*: the
explorer had placed its target on a frontier cell against the doorframe, inside
the wall's inflated cost, where no path can end. Nothing in the inflation needed
changing; the targets did.

## Decision

`find_best_frontier` replaces the nearest-cell rule in `explore`:

1. **Cluster** frontier cells into 8-connected groups — each one an unexplored
   edge of the map. Groups under 4 cells (20 cm) are scan noise and dropped.
2. **Target** of each cluster: its best *eligible* member — ranked by
   **clearance** from occupied cells (capped at 0.5 m, the inflation radius),
   ties broken by nearness to the centroid — skipping members closer than the
   minimum distance to the robot or near an already attempted target. A member
   cell, because a curved frontier's centroid can lie inside a wall; eligible,
   because a cluster must never be discarded just because its single best cell
   is beside the robot (see the correction below).
3. **Score** = cluster size ÷ (1 + distance to its target): the most unexplored
   edge per metre of travel. Minimum distance and previously attempted targets
   are skipped as before.

The old rule stays as `find_nearest_frontier`, selectable with the
`explore_strategy` parameter (`clusters` | `nearest`, re-read per step), so the
comparison below can be repeated. Both are pure Python, layer-1 tested (ADR-018);
selection takes 0.07 s per step on a 20 × 20 m grid.

**Inflation is not changed.** Its claim is withdrawn in ADR-026 with a pointer
here.

## Rationale

Measured live, fresh simulation and fresh SLAM map each time,
`explore(240 s)` from the spawn pose, known area counted from the rendered map
(`eval/exploration_coverage.py`, procedure in EVALUATION.md §4c):

| Strategy | Launch | Known area | Frontiers visited | Planning failures | Paths leaving the costmap* |
|---|---|---|---|---|---|
| nearest | A | 9.1 m² | 9 | 0 | 1020 |
| clusters, single target per cluster | B | 13.2 m² | 5 | 1 | 0 |
| clusters, single target per cluster | C | 12.6 m² | 4 | 0 | 40 |
| clusters, single target per cluster | two more | **nothing — stopped at step 1** | 0 | — | — |
| clusters, eligible clearance target (adopted) | D | **14.1 m²** | 13 | 1 | 0 |

\* `planner_server` "worldToMap failed" log lines: a path running past the
costmap's current edge — the nearest rule produced them in bulk.

- Clusters map **~40–55% more** than the nearest rule in the same time.
- The single-target version was unreliable: in two of its four launches the
  robot stood inside one large frontier whose best cell was beside it, the
  whole cluster was skipped, and exploration ended before moving. Eligible
  targets fix that by construction (layer-1 regression tests).
- **Clearance did not measurably remove planning failures** (one in D). It
  stays because a goal against a wall is the failure mode seen at the doorway,
  but no claim rests on it.
- **One run per launch.** Enough to see a 40% difference and a strategy that
  stops dead; not enough to rank area between the cluster variants.

**Correction (same day).** A first version of this ADR attributed launch C to
the clearance target and claimed it removed planning failures. Launch C in fact
ran the previous code: in this workspace `colcon build --symlink-install` installs
*copies* of Python modules, and that edit had not been rebuilt. Found when the
benchmark rerun hit the stop-at-step-one bug; every live measurement since
starts by checking the installed modules against `src/`.

**Why not raise the planner tolerance instead.** `GridBased.tolerance` is already
0.5 m; the failure happened with it. Choosing a goal that is valid in the first
place beats relying on the planner to repair one.

**Why not lower the inflation.** It was the first hypothesis and the measurement
refuted it. Lowering it would let the robot plan closer to walls it scrapes, for
no measured gain.

**Alternatives rejected:** information-gain frontiers (raycast the unknown area
visible from each target) — better in principle, an order of magnitude more
computation per step in Python, and the simple size-per-metre score already
fixed the observed problem.

## Consequences

- `explore` opens rooms instead of tracing walls; the self-built scene memory
  covers more of the house per minute of exploration.
- New `skills_executor_node` parameter `explore_strategy` (default `clusters`).
- The benchmark is unaffected: it scores plans in `dry_run` and never executes
  `explore` (ADR-013).
- The LIDAR's 3.5 m range still leaves the middle of large rooms unknown; a
  human with WASD (ADR-023) still maps a whole house better. Exploration is
  better, not complete.
