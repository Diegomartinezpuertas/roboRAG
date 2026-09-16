# ADR-029: One owner for /cmd_vel — manual driving outranks Nav2

**Date:** 2026-09-16
**Status:** Accepted

## Context

Two things drove the robot by publishing straight to `/cmd_vel`: Nav2 (through
its velocity smoother and collision monitor) and the dashboard's WASD driving
(ADR-023). With both active the robot obeyed whichever message arrived last —
two controllers at 20 Hz each. ADR-023 recorded it as an accepted limitation:
drive by hand only with `use_nav2:=false`.

## Decision

`cmd_vel_mux_node` (robot_skills) is the only node that publishes `/cmd_vel`:

| Input | Topic | Priority |
|---|---|---|
| Manual driving (dashboard) | `/robot/cmd_vel_manual` | 2 |
| Nav2 (collision monitor output) | `/cmd_vel_nav_out` | 1 |

The highest-priority source that published within its timeout (0.5 s) holds
control; commands from lower sources are dropped while it does. Pressing a key
takes over mid-goal; letting go hands control back once the manual timeout
lapses. The active source (`teleop` | `nav2` | `idle`) is published on
`/robot/cmd_vel_source` (transient local) and shown in the dashboard's drive
panel.

The arbitration is `robot_skills/cmd_vel_mux.py` — pure Python, layer-1 tested —
and the node is launched by `simulation.launch.py` with or without Nav2. Nav2's
`collision_monitor.cmd_vel_out_topic` is `cmd_vel_nav_out`; the dashboard's
`cmd_vel_topic` default is `/robot/cmd_vel_manual`.

**Why that topic name.** The first name, `/cmd_vel_teleop`, was already
subscribed by Nav2's `behavior_server` — the input of its AssistedTeleop
behavior. Found by listing the live topic graph; renamed into the project's
`/robot/<name>` convention.

## Rationale

**Why not `twist_mux`.** It is the standard answer and is not installed here,
and installing system packages needs interactive sudo. The arbitration is a
few lines with their own tests; the dependency can replace them later without
changing any topic.

**Why manual over Nav2.** A person pressing a key during an autonomous goal is
correcting it. The opposite priority would make the manual controls ignore the
person exactly when they are needed.

**Verified live** (headless full stack, Nav2 running): a navigate goal to (3, 0)
started with the mux reporting `nav2`; holding S through the dashboard switched
it to `teleop` and the robot backed from x = 0.72 to 0.64; releasing returned it
to `nav2`, and the goal was reached. `/cmd_vel` carried 131 messages in the
window: 46 manual (−0.18 m/s), the rest Nav2's. A node-level test repeats the
override on real topics.

## Consequences

- WASD works with Nav2 running; `use_nav2:=false` is no longer required to
  drive by hand (it still keeps Nav2 from moving the robot while mapping).
- **`teleop_twist_keyboard` must publish to the mux input**:
  `ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args -p stamped:=true -r cmd_vel:=/robot/cmd_vel_manual`.
  Published straight to `/cmd_vel`, it would bypass the arbitration.
- **Known bypass:** Nav2's `docking_server` also publishes `/cmd_vel` directly —
  `nav2_bringup`'s launch does not remap it. It only publishes during a docking
  action, which this project never requests. Remapping it means copying the
  upstream launch file; recorded rather than done.
- `cmd_vel_mux_node` joins the shutdown contract test (ADR-016).
