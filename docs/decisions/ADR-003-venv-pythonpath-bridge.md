# ADR-003: Bridge agent_env via PYTHONPATH instead of activating the venv

**Date:** 2026-07-14
**Status:** Accepted

## Context

ROS 2 nodes (`rclpy`) are installed for the system Python
(`/usr/bin/python3`, the interpreter `colcon`/`ros2` use), while the agent's
dependencies (`chromadb`, `ollama`, `fastapi`, and indirectly `cv_bridge` via
`numpy`) live in the `agent_env` venv. When building `ament_python` packages
with `colcon build --symlink-install`, the generated `console_scripts` are
pinned to the `#!/usr/bin/python3` shebang regardless of whether `agent_env`
was active in the shell that ran the build.

Activating `agent_env` (which swaps `python3` in `PATH`) before
`ros2 run <pkg> <node>` fixes nothing because the shebang is already baked;
and invoking nodes with the venv's own `python3` fails because it lacks
`rclpy` and the system `cv2`/`cv_bridge`.

## Decision

Never activate `agent_env` for building or running ROS 2 nodes. Instead,
`setup_env.sh` exports:

```bash
export PYTHONPATH="/home/diego/robot_ws/agent_env/lib/python3.12/site-packages:${PYTHONPATH}"
```

so the system Python (the one ROS 2 and the console_scripts expect) also
resolves `chromadb`, `ollama`, `fastapi`, etc.

## Rationale

- Avoids rebuilding the workspace with an interpreter `rclpy` doesn't support.
- Keeps `cv_bridge`/`cv2` (installed via apt for the system Python) working
  without ABI conflicts.

## Consequences

- `numpy` in `agent_env` must stay on the 1.x series (`numpy<2`) to match the
  ABI of the system `cv2` (compiled against NumPy 1.x). If `agent_env` moves
  to NumPy 2.x, `cv_bridge` fails with
  `ModuleNotFoundError: numpy.core.multiarray failed to import`.
- Every new terminal must `source ~/robot_ws/setup_env.sh` (instead of
  activating `agent_env`) before building or launching nodes.
