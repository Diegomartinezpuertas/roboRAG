# ADR-026: Start from a saved map — `saved_map:=<id>`, a demo launch, and zones that follow the session

**Date:** 2026-09-16
**Status:** Accepted — its doorway claim corrected by
[ADR-027](ADR-027-exploration-frontier-clusters.md); its open session item resolved by
[ADR-028](ADR-028-memory-session-per-map-frame.md); its "keeps mapping from there"
replaced by [ADR-035](ADR-035-saved-map-loads-read-only.md) (a saved map loads
read-only; mapping is `saved_map_mode:=mapping`)

## Context

Every run started SLAM on an empty map. For benchmarking that is deliberate
(ADR-013 measures planning on whatever the robot has seen), but for a demo, or
for anyone who clones the repository, it means a mapping phase before anything
interesting can happen — and that phase is where this stack is weakest.

It was measured while trying to produce a map for the demo. Five minutes of the
frontier explorer on the TurtleBot3 house mapped **9.7 × 4.4 m**: the nearest-
frontier rule kept picking cells along the same wall, and a frontier goal it
set beside the doorway failed to plan. *(Corrected in ADR-027: this was first
read as "0.5 m inflation makes a 0.8 m door impassable". Measured afterwards, the
robot crosses that door both ways at 0.5 m; what fails is a goal placed against
a wall, inside the inflated cost.)* Driving explicit waypoints helped, but large
rooms stayed unknown: the LDS-01 reaches 3.5 m, and SLAM Toolbox does not mark
free space along rays that hit nothing. A human with WASD (ADR-023) maps the
house faster and better than the explorer does.

Saving a map already worked (ADR-019: `save_map` serializes the pose graph). What
did not exist was the way back: ADR-019 left the reload as "a documented
two-parameter procedure", a `saved_map:=<id>` wrapper as follow-up. Yet the
`save_map` skill's result and the dashboard's "Guardar mapa" confirmation both
told the user to *relaunch with `saved_map:=<id>`* — an argument no launch file
had. An instruction the system gave was false.

## Decision

**`saved_map:=<id>` on `full_system.launch.py`** (and on `simulation.launch.py`
and `agent.launch.py`, which it forwards to). One id, two consumers:

- **SLAM** loads the pose graph. The stock SLAM Toolbox launch takes only a
  parameters file, so `simulation.launch.py` rewrites the project's
  `slam_params.yaml` into a temporary copy with `map_file_name` and
  `map_start_at_dock: true` — the robot starts at the graph's first node, the
  spawn pose mapping began at — and keeps mapping from there.
- **rag_node** pins its memory session to the same id (`map_session_id`), so the
  coordinate memories written on that map are the ones retrieved (ADR-019).

The map is looked up in `$ROBOT_WS/data/maps/<id>/` first (maps saved on this
machine), then in `robot_bringup/maps/<id>/` (maps shipped with the
repository, installed by `setup.py`). An unknown id **fails the launch** with the
paths searched. The rules live in `robot_bringup/saved_maps.py`, pure Python,
layer-1 tested (ADR-018).

**`demo.launch.py`** — `full_system` the way the demo is recorded: Gazebo GUI
on, RViz on, `saved_map:=house`. Every default is overridable.

**Zones follow the session.** Zones live in SQLite with no session, but their
memory documents are tagged with the session they were indexed under. Pinning a
saved map's id would have dropped every zone out of retrieval — "ve donde se
suele cocinar" included (ADR-022). `dashboard_node` now indexes every stored
zone once `/rag/update_map` answers after it starts: an idempotent upsert by
zone id into whatever session is active.

**`house` is the demo's map id.** Map the house by hand (`use_nav2:=false`,
WASD), save it from the dashboard as `house`, and `demo.launch.py` loads it with
no arguments. Shipping it with the repository is copying
`data/maps/house/map.{posegraph,data}` into `src/robot_bringup/maps/house/`.

## Rationale

**Why rewrite the params file rather than launch SLAM Toolbox directly.** The
async node is a lifecycle node whose configure/activate sequencing the stock
launch file already handles. Replacing that include to pass two parameters
would copy its lifecycle wiring into this project and drift from upstream. A
rewritten copy of one YAML file is the smallest change.

**Why the dock start.** `map_start_pose` would have to be kept in sync with the
spawn pose in two places. The first node of a graph mapped from the spawn *is*
the spawn — as long as the robot spawns where mapping began, which
`simulation.launch.py`'s fixed `x_pose`/`y_pose` guarantee.

**Why the workspace wins over the shipped map.** A user who re-maps and saves as
`house` should get their map without editing the repository; the shipped one
stays a fallback for fresh clones.

**Why fail on an unknown id.** The alternative — SLAM silently starting empty —
produces a demo whose memories point at a map that is not loaded, which looks
like a RAG failure and is not one.

**Why the Gazebo GUI only in the demo launch.** On WSL2 it renders on llvmpipe
and costs real-time factor — ~0.15 on the setup that decided it was off by
default; measured on the current one (2026-09-16, before the camera and voxel
changes of ADR-037), ~0.68 headless and ~0.59
with the window. A recording wants the window; everyday runs want the speed.

## Consequences

- The instruction the dashboard and `save_map` give is now true.
- A demo can start without a mapping phase, and a fresh clone can too once a map
  is shipped in `robot_bringup/maps/`.
- Starting from a saved map re-pins the persisted session. A later launch
  *without* `saved_map` continues that session on a fresh map — which is what
  every launch did before, since nothing ever calls `MapSession.rotate()`. That
  gap predates this ADR and is recorded as an open item: rotating on every fresh
  map would be correct in principle, and would discard the memory of everyone
  whose sim always spawns at the same pose. *(Resolved in ADR-028: a fresh map's
  session is named after its frame — world plus spawn pose.)*
- `simulation.launch.py` writes one small temporary YAML per launch with a saved
  map.
- ~~The doorway finding — `inflation_radius: 0.5` makes a 0.8 m door impassable~~
  Withdrawn: measured in ADR-027, the robot crosses the door at 0.5 m in both
  directions. The real failure — frontier goals placed against walls — is fixed
  there by choosing targets with clearance.
