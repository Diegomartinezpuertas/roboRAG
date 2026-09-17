# ADR-034: Refuse to launch a simulation while another Gazebo server is running

**Date:** 2026-09-17
**Status:** Accepted

## Context

While preparing the demo, the map "house" was loaded with `demo.launch.py` and
the robot toured its rooms. Within minutes the live map showed the clean house
next to a second copy, rotated and offset, and the known area grew from about
120 m² to 147 m². The saved files were untouched.

The cause was a second simulation. A `gz sim` server from the mapping session
half an hour earlier was still running, with no launch left to stop it, next to
the demo's own. Both published on the same gz-transport topics, so the ros_gz
bridge forwarded two robots' scans, odometry and clock, and SLAM Toolbox mapped
both into one graph.

How the server outlived its launch was reproduced with `ros_gz_sim`'s
`gz_sim.launch.py`, the same include `simulation.launch.py` uses. It starts
Gazebo through `/bin/sh -c`, and the server is a `ruby gz sim` child of that
shell:

| How the launch is stopped | Launch | `sh` wrapper | `ruby gz sim` server |
|---|---|---|---|
| Ctrl+C (SIGINT to the terminal's process group) | exits | exits | **exits** |
| Terminal closed (SIGHUP to the group) | exits | exits | **keeps running** |

Nothing in the new launch notices: the server is headless, publishes nothing
into ROS by itself, and only shows up as a corrupted map once a bridge in the
same partition is running.

## Decision

1. **`simulation.launch.py` fails at once when a Gazebo server is already running
   in its partition.** The check runs before its own server starts, so any
   server found belongs to another simulation. The message gives the PIDs, why
   it matters, and how to stop them: Ctrl+C the other launch, or `kill <pid>`
   when its terminal is gone. `full_system.launch.py` and `demo.launch.py`
   include it, so every simulated launch is covered.
2. **The logic is pure and tested in layer 1** (`robot_bringup/sim_guard.py`,
   ADR-018). It reads `/proc`: a process counts when its arguments are
   `gz sim` without `-g`, in either shape `/proc` shows (ruby rewrites its title
   into one string). The shell wrapper and the GUI client do not count.
3. **Only the same partition counts.** A server whose `GZ_PARTITION` differs
   cannot reach this simulation's bridge, so running two simulations on purpose
   stays possible with a partition (and a `ROS_DOMAIN_ID`) of its own. A process
   whose environment cannot be read belongs to another user, whose default
   partition differs, and is not counted.

## Rationale

**Rejected — kill the old server automatically.** It might not be an orphan: a
second `ros2 launch` in another terminal finds the first one's live server. A
launch that stops someone else's simulation is worse than one that refuses
with the command to type.

**Rejected — start Gazebo without the shell wrapper** (an own `ExecuteProcess`
with `shell=False`), so the launch signals the server itself. It removes one
way to create an orphan but not the others (a crashed launch, a killed
process), and it duplicates the environment setup `gz_sim.launch.py` does. The
guard catches an orphan however it came to exist. The wrapper stays a possible
follow-up.

**Rejected — only warn.** By the time anyone reads a warning, SLAM has already
received the other robot's scans. A saved map cannot be un-corrupted in a
running session; the launch has to be restarted anyway.

## Consequences

- Verified live: with a stray `gz sim -s` running, `ros2 launch robot_bringup
  simulation.launch.py` exits with code 1 and the message, and no second server
  starts.
- A server running under another user with the same explicit `GZ_PARTITION`
  is missed. That is not a setup this project has.
- Tests: `tests/test_sim_guard.py` (both argument shapes, ruby prefix, shell
  wrapper and GUI client excluded, partitions, unreadable and vanished
  processes, the message).
