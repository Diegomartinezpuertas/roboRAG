# ADR-037: Stop paying for pixels and voxels nobody reads — real-time factor 0.47 → 0.90

**Date:** 2026-09-17
**Status:** Accepted

## Context

"Navigation is not smooth" was the last blocker before recording the demo, and
it was not Nav2. The simulation was running at a **real-time factor of 0.47**
with the dashboard and RViz open (no Gazebo window): every motion, every
rotation and every Nav2 control cycle played at half speed. Gazebo's server was
using 350% of CPU and RViz 141%, on a machine with 20 cores whose load average
sat at 13–15.

Two of those costs bought nothing:

- **The robot's camera ran at 30 Hz**, 640×480, `always_on`, rendered on
  llvmpipe because WSL2 gives Gazebo no GPU (ADR-009 already cut its
  resolution). The only consumers are `perceive` and `scan_360`, which sample
  **one frame** when a skill runs.
- **Both costmaps ran `voxel_layer` as well as `obstacle_layer`**, and both
  layers' only observation source was the same 2D LIDAR scan. The voxel layer
  maintained a 3D grid of a plane the obstacle layer had already marked and
  cleared. Inherited from TurtleBot3's parameter file.

## Decision

1. **The camera runs at 5 Hz** (`model.sdf`), a 200 ms refresh — more than the
   skills that sample it need, and still live enough to watch in RViz or the
   dashboard.
2. **`voxel_layer` is removed from both costmaps.** `obstacle_layer` keeps the
   same `scan` source, marking and clearing as before.

## Rationale

**Why not turn the camera off between skills.** It would be faster still, but
`always_on: false` in Gazebo means the sensor only updates while something
subscribes, and the bridge subscribes permanently; making that conditional adds
a moving part to the launch for a cost 5 Hz already removes.

**Why not lower the LIDAR's 10 Hz too.** SLAM Toolbox and both costmaps consume
every scan, and `minimum_time_interval: 0.5` already throttles what SLAM keeps.
The LIDAR is also cheap next to a camera: 360 rays against 307k pixels.

**Why not keep the voxel layer for future 3D perception.** There is no 3D
source in this stack: the depth camera is not bridged, and the scene descriptor
works on the 2D scan and the colour image (ADR-014). When a pointcloud source
appears, the layer comes back with it.

**Rejected — closing RViz.** It is 96–141% of CPU, and the dashboard shows the
same map, but RViz is what the video uses to show the LIDAR and the costmaps.
The launch arguments already make it optional per take (`use_rviz:=false`).

## Consequences

Measured on the same house, the same read-only load, dashboard + RViz open and
no Gazebo window, sampling the factor the dashboard publishes:

| | Before | After |
|---|---|---|
| Real-time factor | 0.46–0.47 | **0.89–0.95** |
| Gazebo server CPU | 350% | 221% |
| RViz CPU | 141% | 96% |

And on the three goals the demo records, run for real (navigate + perceive,
door to door):

| Goal | Before | After |
|---|---|---|
| "Ve a estacion_b" | 70 s | **34 s** |
| "Llévame a donde había muchos objetos" | 9 s | 9 s |
| "Ve donde se suele cocinar" | 37 s | **27 s** |

- **Every earlier real-time-factor number in these docs was measured with the
  30 Hz camera and the voxel layers** (~0.68 headless, ~0.59 with the Gazebo
  window, 0.40–0.47 with RViz too). They stay as the history of the change;
  the current configuration is the table above.
- **`perceive` sees a frame up to 200 ms old.** For a robot that stops before
  describing what it sees, that is invisible; if a future skill needs motion in
  the image, the rate is one number in `model.sdf`.
- **The local costmap now has one obstacle source.** Anything the LIDAR cannot
  see — a table top, a step — was already invisible to it: the voxel layer was
  fed the same scan.
- The benchmark is unaffected: it scores planning, not execution (ADR-013), and
  the planner's latency is dominated by Ollama.
