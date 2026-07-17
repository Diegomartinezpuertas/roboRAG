# ADR-006: CycloneDDS pinned to loopback on WSL2

**Date:** 2026-07-14
**Status:** Accepted

## Context

WSL2 exposes several network interfaces (`lo`, `eth0`, `docker0`). By default
CycloneDDS announces every participant on all of them, which produced
intermittent pub/sub discovery between local nodes (goals published on
`/robot/goal` sometimes never reached the subscriber).

## Decision

Pin CycloneDDS to the `lo` interface via `~/robot_ws/cyclonedds.xml`,
referenced with `CYCLONEDDS_URI` in `setup_env.sh`. Everything runs on one
machine, so loopback is sufficient.

## Consequences

- Deterministic DDS discovery between all local nodes.
- If an external robot/PC ever joins over the network, the corresponding
  interface must be added to the XML (or the restriction removed).

## Related note (it was NOT DDS)

The "duplicated node" symptom in `ros2 node list`
(`/skills_executor_node` twice) had a different cause: `name:=` in a launch
file remaps **every** node in the process, including
nav2_simple_commander's internal `BasicNavigator`, renaming it to
`skills_executor_node` too. Fix: don't pass `name=` for
`skills_executor_node` in `agent.launch.py` (the node names itself, and
`BasicNavigator` keeps its `basic_navigator` name).
