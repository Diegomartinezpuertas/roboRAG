# ADR-007: MultiThreadedExecutor + callback groups for blocking calls

**Date:** 2026-07-14
**Status:** Accepted

## Context

`llm_planner_node` and `skills_executor_node` make ROS 2 service calls *from
inside* callbacks (the planner calls `/skills/execute` from the
`/robot/goal` callback; skills calls `/rag/update_map` from the
`/skills/execute` callback). With a single-threaded executor this cannot be
resolved by waiting inside the callback: the only thread that could process
the response is busy running the callback itself.

Two failed approaches preceded the final one:

1. Nested `rclpy.spin_until_future_complete(node, ...)` →
   `RuntimeError: Executor is already spinning`.
2. Throwaway executor (`SingleThreadedExecutor()` + `add_node/remove_node`
   around each wait) → **the node goes deaf** when the callback returns:
   `add_node` transfers ownership of the node's entities (subscriptions
   included) to the temporary executor, and the main executor stops
   dispatching them. This was the real failure behind "lost" goals that was
   initially blamed on DDS discovery.

## Decision

- Every node with blocking calls runs on its **own `MultiThreadedExecutor`**
  (created in `main()`, never the process-global one).
- **Service clients** (and subscriptions that must stay alive during a long
  callback, such as `/map` and the camera during `explore`) go in a
  **separate callback group** (`MutuallyExclusiveCallbackGroup` for the
  planner's clients, `ReentrantCallbackGroup` for the skills node's I/O
  group). Everything else stays in the default group, which serializes skill
  execution.
- Waits use **`threading.Event`** on `future.add_done_callback` (with a
  timeout and `future.cancel()`), touching no executor.
- The **process-global executor stays free** for
  `nav2_simple_commander.BasicNavigator`, which internally calls
  `rclpy.spin_until_future_complete(self, ...)` on it.
- `TransformListener` uses the default `spin_thread=False`: with `True`,
  tf2_ros adds *the whole node* to its own executor thread — the same
  entity-stealing failure (observed: it silenced the camera subscription).

## Consequences

- Forbidden in this repo: `rclpy.spin_*` inside callbacks and the throwaway
  executor pattern.
- Skills still execute one at a time (mutually exclusive default group) —
  observable behavior unchanged.
