# ADR-023: Driving the robot from the browser, with a deadman

**Date:** 2026-09-15
**Status:** Accepted — extended by
[ADR-029](ADR-029-cmd-vel-mux.md) (manual driving now outranks Nav2 through a mux)

## Context

Every capability in this project assumed a map already existed, or that the
robot would build one by exploring autonomously. Frontier exploration
(`explore_skill`) does work, but it is slow, it wanders, and on this stack it
inherits the navigation unreliability the review is explicit about (REVIEW §6.2:
narrow doorways, software physics). A person who wants a map of the house in
three minutes — before asking the agent anything — had no way to get one from
the system's own interface. They had to know to run
`ros2 run teleop_twist_keyboard teleop_twist_keyboard` in a second terminal, with
the right `RMW_IMPLEMENTATION`, and to know that it publishes the wrong message
type for this stack (see below).

Manual driving is also what makes ADR-022 usable: rooms get their names from a
human walking the robot through them and saying "this is the kitchen".

## Decision

The dashboard drives the robot. A **WASD** panel under the map publishes to
`/cmd_vel`, paired with a **📍 Marcar zona aquí** button that names the room the
robot is standing in and a **💾 Guardar mapa** button that persists the map it
has just built (ADR-019).

Three properties make this safe enough to expose over unauthenticated HTTP on
loopback:

1. **The browser sends keys, never velocities.** `POST /api/teleop {"keys":
   ["w","a"]}`. Speeds come from the node's parameters and are clamped to the
   Waffle's documented maxima, 0.26 m/s and 1.82 rad/s. What a client can
   request is bounded by the robot's configuration, not by its own JSON.
   *(2026-09-17: the defaults were 0.18 m/s and 1.0 rad/s, ×1.4 with shift.
   Mapping the house by hand is quicker at full speed, so the defaults are now
   the maxima themselves and `teleop.py` clamps every command to them — a
   parameter typo can no longer ask the base for more than it has. Shift still
   boosts, up to the same ceiling.)*
2. **A deadman, not a latch.** Held keys are re-sent every 150 ms. A command not
   refreshed within `teleop_timeout_sec` (0.6 s) expires, and the node publishes
   a stop — three times, because `/cmd_vel` reaches the simulator through a
   best-effort bridge and a dropped stop is the one dropped message that
   matters. Closing the tab, sleeping the laptop or losing the network stops the
   robot within about 11 cm of travel (15 cm boosted).
3. **Silence when idle.** While nobody is driving, the node publishes *nothing*.
   `/cmd_vel` belongs to Nav2 the rest of the time, and an open dashboard cannot
   interfere with an autonomous goal.

The velocity logic and the deadman live in `robot_dashboard/teleop.py`, free of
ROS imports, and are unit-tested in layer 1 (ADR-018). The node owns only the
publisher and the 20 Hz timer.

**Message type:** `geometry_msgs/TwistStamped` by default, not `Twist`. This
stack's `ros_gz_bridge` config maps `TwistStamped` → `gz.msgs.Twist`, and Nav2
runs with `enable_stamped_cmd_vel: true`; a plain `Twist` would be published
into a topic with no subscriber and the robot would sit still while the UI
reported motion. `cmd_vel_stamped: false` restores the classic form for a
different base.

## Rationale

**Why in the dashboard rather than a launch-file teleop node.** The dashboard is
already the thing a person has open, on the Windows side of WSL2, with the map
in front of them. Teleop belongs where the map is: drive, watch SLAM fill in,
name the room you are in, save. A separate terminal tool cannot offer the middle
two steps.

**Why not `teleop_twist_keyboard`.** It is fine, and it stays available. It also
publishes `Twist` (wrong type here), needs its own sourced shell, and knows
nothing about zones or the map session. The decision is not to replace it but to
make the common path work from the UI that already exists.

**Why a deadman at all, when a keyup handler exists.** Because the keyup is the
event that does *not* arrive in every failure worth designing for: the tab is
closed mid-press, the browser is backgrounded on a phone, the laptop sleeps, the
Wi-Fi drops. The browser also sends a stop on `blur` and on release — the
deadman is what covers the cases where the browser sends nothing at all.

**Why 0.6 s.** Four refresh intervals. Short enough that an abandoned session
stops within ~11 cm at the default speed; long enough to survive a couple of dropped
requests on a loopback connection without the robot stuttering.

**Alternatives rejected:**

- *WebSocket instead of polling POSTs.* Lower latency and a free liveness
  signal, but it adds a protocol to a dashboard whose whole design is one
  polling loop over plain endpoints (ADR-005), and the deadman would still be
  needed for a socket that stays open while the page is frozen.
- *Sending velocities from the browser.* Would let the UI offer analog control
  (a joystick), at the cost of making the robot's speed limit a client-side
  concern. Keys in, velocity out, ceiling on the robot.
- *Latching a command until an explicit stop.* Simpler, and one lost stop
  message means a robot driving into a wall.

## Consequences

- A user can map the house by hand before involving the agent at all:
  `use_nav2:=false` for a mapping-only run, drive, name the rooms, save the map,
  then relaunch with the agent and the memories still pointing at real places.
- New dashboard parameters: `cmd_vel_topic`, `cmd_vel_stamped`,
  `teleop_linear_speed`, `teleop_angular_speed`, `teleop_timeout_sec`,
  `teleop_rate_hz`. The speeds are re-read per command, so `ros2 param set`
  retunes them mid-session.
- The dashboard's HTTP API can now *move the robot*, which sharpens the existing
  loopback-only default (`http_host: 127.0.0.1`). The warning in
  `agent_params.yaml` and `api_reference.md` applies with more force: exposing
  this port on a network hands over the controls.
- Driving during an active Nav2 goal makes both publish to `/cmd_vel`, and the
  result is whatever arrives last. This is not arbitrated: teleop is for mapping
  runs, and the UI says so. A `twist_mux` is the principled fix if the two ever
  need to coexist.
  *(Resolved in [ADR-029](ADR-029-cmd-vel-mux.md): `cmd_vel_mux_node` now owns `/cmd_vel`,
  manual driving over Nav2, verified live mid-goal.)*

- `robot_dashboard` gains a `geometry_msgs` dependency.
