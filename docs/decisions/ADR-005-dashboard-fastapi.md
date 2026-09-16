# ADR-005: Web dashboard with FastAPI embedded in a ROS 2 node

**Date:** 2026-07-14
**Status:** Accepted — extended by
[ADR-023](ADR-023-browser-teleop.md) (manual driving) and
[ADR-024](ADR-024-memory-inspection-service.md) (memory viewer)

## Context

The project needs real-time observability of the cognitive pipeline (goal →
plan → skills → response), access to every node's logs, and a command input
channel that also works for voice. Options considered: rosbridge_suite + an
external page, RViz plugins, or an embedded HTTP server.

## Decision

New package `robot_dashboard` with a single node (`dashboard_node`) that:

- Runs FastAPI + uvicorn on a daemon thread inside the ROS 2 node itself.
- Keeps a thread-safe ring buffer of events fed by subscriptions to
  `/robot/goal`, `/robot/status`, `/robot/response`, `/rosout`, and `/map`.
- Serves an embedded SPA (HTML string in `web_page.py`) that polls
  `GET /api/events?since=<id>` every ~700 ms and publishes goals via
  `POST /api/goal`.
- Voice input uses the browser's Web Speech API (Chrome/Edge, `lang=es-ES`) —
  recognition runs in the Windows browser, consuming no robot VRAM and not
  depending on the NPU (unreachable from WSL2).

## Rationale

- **REST polling vs WebSocket/rosbridge:** for a single-user local panel,
  polling removes all asyncio↔rclpy threading complexity (one `Lock`
  suffices). rosbridge would add another process and a generic protocol this
  project doesn't need.
- **Embedded HTML string:** avoids `data_files` handling and works directly
  with `--symlink-install`.
- **Voice in the browser:** available today without loading Whisper; the
  native voice phase (Whisper + NPU on Windows) remains future work and will
  publish to the same `/robot/goal`.

## Consequences

- Local use only (no auth); do not expose port 8080 outside the machine.
  Since [ADR-023](ADR-023-browser-teleop.md) the API can also *command motion*,
  so this is no longer a privacy rule: exposing the port hands over the
  controls. The default bind address is loopback for that reason.
- Event latency ≤ ~700 ms (polling interval), fine for human observability.
- Voice input requires Chrome/Edge; Firefox does not implement Web Speech.
