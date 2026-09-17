# ADR-036: Nav2's robot radius — as large as the house admits, not as small as the vendor file says

**Date:** 2026-09-17
**Status:** Accepted

## Context

Two hand-made maps of the house were ruined while being used, and the cause was
not SLAM. Driving a fresh map along the house's long hall with Nav2 (12
waypoints, ground truth read from Gazebo at every stop) the robot **wedged
against an obstacle for 251 s**. Its wheels kept turning: odometry gained
**99° of heading error and 2.9 m of position error**, and SLAM Toolbox, which
had been within 3 cm until then, was left 0.5–0.8 m out for the rest of the run.

That is the mechanism behind the corrupted maps. A saved map's walls double when
the robot returns, because the scans of the second visit are placed by a pose
the slip has moved ([ADR-035](ADR-035-saved-map-loads-read-only.md) measured the
first map 0.31 m out along that hall, with its heading intact — the signature of
a straight-line slip).

The reason the robot touches anything at all was in the costmaps:

| | Value |
|---|---|
| `robot_radius` in `nav2_params.yaml` | **0.15 m** |
| Waffle collision box in `model.sdf` | 0.265 × 0.265 m, centred 0.064 m behind `base_footprint` |
| Circumscribed radius that follows | **0.237 m** |
| `robot_radius` in Nav2's own default params, and TurtleBot3's Humble params | 0.22 m |
| `robot_radius` in TurtleBot3's Jazzy params, where this project's copy came from | 0.15 m |

At 0.15 m the planner and the controller believe the robot's rear corners are
9 cm closer in than they are, so a path may graze a wall and a rotation in place
may catch a door frame.

The geometric value cannot simply be used, though. At 0.24 the house became
**impassable**: every room goal failed to plan from the start pose and the robot
never moved. The bottleneck is the spawn pose itself — the robot starts **0.25 m
from the hall's wall** (confirmed live: minimum scan range 0.25 m, median 0.44 m)
— and the tight nook around it is partly furniture that the map does not hold.

Asking the planner itself, live, for a path to each of the four rooms while
changing `robot_radius` on both costmaps:

| `robot_radius` | entrada | baño | salón | cocina |
|---|---|---|---|---|
| 0.22 | no plan | no plan | no plan | no plan |
| 0.20 | path | path | path | path |
| 0.18 | path | path | path | path |
| 0.15 | path | path | path | path |

Two offline analyses of the saved map (an exact distance transform plus
reachability from the spawn) had put the cap at 0.22 — they miss what the live
costmap adds from the LIDAR. The planner is the authority, and 0.20 is the
largest radius it can work with here.

## Decision

**`robot_radius: 0.20`** in both costmaps — the largest value that still plans
in this house — replacing the 0.15 inherited from
`turtlebot3_navigation2`'s Jazzy parameter file.

The 3.7 cm between 0.20 and the model's circumscribed 0.237 is accepted and
recorded here: a rotation in place with the robot's back against a wall can
still catch it. What the change buys is measured below; what it does not buy is
a guarantee.

## Rationale

**Why not 0.24, the model's geometry, or 0.22, Nav2's default.** Neither plans a
path out of the spawn pose in this house. Measured above, live, against the
planner.

**Why not move the spawn pose to somewhere roomier.** The spawn *is* the map
frame: every saved map and every memory session id is defined by world plus
spawn pose (ADR-019, ADR-028). Moving it invalidates the saved house map and
every coordinate memory written on it.

**Why not a footprint polygon.** More accurate for an off-centre chassis, and
the costmaps support it — but the inflation that protects the robot is built
from the polygon's *inscribed* radius, which for this chassis is its front
overhang (0.07 m). With DWB's `consider_footprint: false`, paths would hug
walls again. A polygon plus footprint-aware collision checking is a measured
follow-up, not a same-day change.

**Why not treat it as a simulator problem.** Wheel slip while pushing is real
robot behaviour; a real Waffle's odometry degrades the same way. The fix belongs
where the wrong number was.

**Rejected — recover after the fact.** SLAM Toolbox cannot remove the nodes a
slip poisoned: in mapping mode the bad scans stay in the graph, and loop closure
only sometimes pulls them back. Once a map is corrupted it has to be mapped
again, which is why this is a prevention fix.

## Consequences

Measured on the same 12-waypoint hallway drive, one run per radius:

| | `robot_radius: 0.15` | `robot_radius: 0.24` |
|---|---|---|
| Wedged against an obstacle | yes, 251 s | no |
| Worst odometry error | 99°, 2.9 m | 3.7°, 0.15 m |
| Worst SLAM error vs ground truth | 0.79 m | **0.06 m** |
| Waypoints reached | 6 / 12 | 11 / 12 |

That drive is what showed the slip, and 0.24 is what stopped it — but 0.24 does
not plan out of the spawn pose, so the shipped value is 0.20, between the two.

- **A goal the robot cannot reach safely now fails instead of being forced.**
  One waypoint (placed 0.3 m from a wall) was refused at both radii; with the
  larger one the robot stopped trying rather than pushing. A refusal does not
  corrupt a map.
- **Tight spots cost time.** Two legs took 109 s and 269 s at 0.24, against
  5–30 s in the open: the extra clearance narrows the routes through this
  house's doorways.
- **The cap was the start pose, not the house.** Once runs start in a named
  zone instead of the spawn nook ([ADR-035](ADR-035-saved-map-loads-read-only.md)),
  the planner finds paths to every zone at 0.24 as well. The shipped value stays
  **0.20** because a run on a *fresh* map still begins in that nook — the
  benchmark's live seeding does — and 0.22 upwards cannot leave it.
- **A house can be too tight for a correct footprint, and this one is.** The
  radius is set by the narrowest place the robot has to leave, not by the robot.
  The check is worth repeating on a new map or a new spawn, and the way to run
  it is to ask the planner for a path to each zone while changing
  `robot_radius` on both costmaps — offline analysis of `map.pgm` alone was
  optimistic by 2 cm, because the live costmap also holds what the LIDAR sees
  and the map does not.
- Nothing in the benchmark changes: it scores the planning decision, not
  execution (ADR-013).
- Manual driving does not go through Nav2, so this fix does not protect a
  mapping run. Driving into furniture with WASD produces the same slip, and
  that is how both maps were lost. A LIDAR brake on the manual input is the
  obvious follow-up; for now the mapping instructions say not to push.
