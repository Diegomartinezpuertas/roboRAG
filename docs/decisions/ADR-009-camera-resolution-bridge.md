# ADR-009: Own camera model at 640×480 and explicit bridge (never touch /opt/ros)

**Date:** 2026-07-14
**Status:** Accepted — the VLM it mentions was later removed
([ADR-014](ADR-014-classical-scene-descriptor.md)); the frame-drop fix still
matters for the classical scene descriptor that replaced it

## Context

`perceive` always failed with "No camera frame received yet", even without
any prior navigation (ruling out thread-starvation hypotheses). Diagnosis via
`ros2 topic bw /camera/image_raw`: the stock TurtleBot3 Waffle SDF model in
`turtlebot3_gazebo` publishes the camera at **1920×1080 (6.2 MB/frame, ~9 Hz,
~55 MB/s)**. With BEST_EFFORT QoS and `skills_executor_node` sharing its
executor with Nav2, TF, and HTTP clients to Ollama and ChromaDB, frames were
dropped at the DDS layer before ever reaching the callback — not a code bug,
a data-volume problem.

Additionally, while rewriting `simulation.launch.py` to use an own model
copy, a second real bug slipped in: the `parameter_bridge` YAML path was
wrong (`waffle_bridge.yaml` instead of `turtlebot3_waffle_bridge.yaml`),
taking down the whole Gazebo↔ROS 2 bridge (`/odom`, `/tf`, `/scan`,
`/cmd_vel`) with only a single easy-to-miss ERROR line — hence no map either
("Frame [map] does not exist").

## Decision

- Copy `turtlebot3_waffle/model.sdf` into `robot_bringup/models/` (never edit
  `/opt/ros/jazzy/`, per CLAUDE.md) and lower the camera to **640×480**
  (~30× less data per frame).
- `simulation.launch.py` no longer includes the stock
  `spawn_turtlebot3.launch.py`: it spawns our `model.sdf` directly and wires
  `parameter_bridge` + `image_bridge` by hand with the correct stock YAML
  path (topic mappings can be referenced without copying).

## Rationale

- 640×480 is plenty for Qwen2.5-VL input (which rescales internally anyway)
  and removes the bandwidth bottleneck without touching the system package.

## Consequences

*(2026-09-17: the same camera also ran at 30 Hz, rendered on the CPU. At 5 Hz
the simulation's real-time factor went from 0.47 to 0.90 — the skills sample one
frame at a time, so nothing downstream noticed. See
[ADR-037](ADR-037-pay-for-the-real-time-factor.md).)*

- If the system's `turtlebot3_gazebo` is upgraded, our model copy can drift
  from upstream fixes — diff occasionally.
- Any future high-resolution sensor must be checked with `ros2 topic bw`
  before assuming "nothing arrives" is a code bug.
