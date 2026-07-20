# ADR-016: Node shutdown contract (`ExternalShutdownException` + guarded `shutdown()`)

**Date:** 2026-07-20
**Status:** Accepted

## Context

All four nodes shared the same `main()` shape:

```python
try:
    rclpy.spin(node)          # or executor.spin()
except KeyboardInterrupt:
    pass
finally:
    node.destroy_node()
    rclpy.shutdown()
```

This handles exactly one shutdown path — a Ctrl-C delivered to a node running
in the foreground of its own terminal — and mishandles the one the project
actually uses. Under `ros2 launch`, shutdown arrives as SIGINT/SIGTERM to the
child processes, which `rclpy` surfaces as `ExternalShutdownException` from
`spin()`, not as `KeyboardInterrupt`. The result was a two-stage failure,
reproduced by sending SIGTERM to `dashboard_node`:

1. `ExternalShutdownException` escapes the `except` clause;
2. the `finally` block then calls `rclpy.shutdown()` on a context that the
   signal handler has already torn down, which raises
   `RCLError: failed to shutdown: rcl_shutdown already called on the given
   context` — a second traceback stacked on top of the first.

Every stop of the stack therefore printed two tracebacks per node, eight in
total. The cost was not cosmetic. `agent.launch.py` runs all four nodes with
`respawn=True`, and a stack trace on exit is indistinguishable in the log from
the genuine crash that respawn exists to recover from, which made the launch
stack read as flaky and buried real errors during debugging.

## Decision

Every node `main()` follows one contract:

```python
try:
    executor.spin()
except (KeyboardInterrupt, ExternalShutdownException):
    pass
finally:
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
```

Both shutdown signals are treated as ordinary termination, and `rclpy.shutdown()`
is called only if the context is still up. Applied identically to `rag_node`,
`skills_executor_node`, `llm_planner_node` and `dashboard_node`.

## Rationale

`ExternalShutdownException` is `rclpy`'s designated signal that the process was
asked to stop from outside; catching it alongside `KeyboardInterrupt` states
that both are normal exits. The `rclpy.ok()` guard is what upstream ROS 2
examples use for the same reason: the signal handler may already have shut the
context down, and `shutdown()` is not idempotent.

**Alternative rejected: `rclpy.try_shutdown()`.** It is idempotent and would
remove the guard, but it is less explicit about *why* the call is conditional,
and the `rclpy.ok()` form matches the demo nodes most readers will recognise.

**Alternative rejected: letting the exception propagate.** `rclpy` installs a
handler that exits with the conventional status for the signal, so bare
propagation is defensible — but it still prints the traceback, which is the
problem being solved.

## Consequences

- Stopping the stack with Ctrl-C now exits silently on all four nodes; anything
  that appears in the launch log at shutdown is a real fault.
- `respawn=True` regains its diagnostic value: a traceback in the log now means
  a node actually crashed.
- New nodes must follow this contract. It is the shutdown half of the executor
  discipline in [ADR-007](ADR-007-executors-callback-groups.md); the two
  together define how a node in this project starts, spins, and stops.
- Not covered by the unit suite — `tests/` runs without a ROS install, so this
  is verified manually (`kill -INT <pid>`, confirm a clean log and exit).
