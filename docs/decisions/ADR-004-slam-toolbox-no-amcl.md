# ADR-004: Localization via SLAM Toolbox, no AMCL

**Date:** 2026-07-14
**Status:** Accepted

## Context

The navigation stack uses Nav2 + SLAM Toolbox with a live, growing map — not
a pre-built static map. `skills_executor_node` needs the robot's current pose
to associate perceived objects (`perceive`) and frontiers (`explore`) with
map coordinates.

## Decision

`skills_executor_node` obtains the robot pose by looking up the
`map -> base_link` transform (published by SLAM Toolbox while scan matching)
instead of subscribing to `/amcl_pose`.

`simulation.launch.py` launches `nav2_bringup/navigation_launch.py`
(controller, planner, behaviors, bt_navigator — without `map_server` or
`amcl`), since SLAM Toolbox already provides the map and the map→odom
transform.

Nav2's `BasicNavigator.waitUntilNav2Active()` is called with
`localizer='slam_toolbox'` — the default `'amcl'` would block forever waiting
for a node that never starts.

## Rationale

- AMCL requires a pre-built static map (`map_server` serving a
  `.yaml`/`.pgm`); this project maps live with SLAM Toolbox, so `/amcl_pose`
  would never be published.

## Consequences

- If the project later switches to localization on a fixed map saved with
  `map_saver_cli`, `nav2_bringup/bringup_launch.py` (with AMCL) would be
  added and the TF lookup revisited.
  *(2026-09-17: localization on a fixed map arrived without AMCL. A saved
  pose graph loads in SLAM Toolbox's own localization mode, so
  `localizer='slam_toolbox'` still holds — ADR-035.)*
