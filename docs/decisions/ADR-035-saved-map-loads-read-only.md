# ADR-035: A saved map loads read-only, and a run starts in a named zone

**Date:** 2026-09-17
**Status:** Accepted — changes ADR-026's load behaviour (it kept mapping).
Read-only only became usable once runs stopped starting in the spawn nook;
both halves are measured below

## Context

ADR-026 loads a saved map into SLAM Toolbox with `map_start_at_dock` and
**keeps mapping** from there, "so the loaded map extends rather than freezes".
Preparing the demo showed what that costs.

The house was mapped by hand and saved as `house`. It was loaded with
`demo.launch.py`, and the preflight toured the named rooms: navigate to each,
then a `scan_360` turn in place. With only one simulation running (ADR-034 rules
out the other cause), the walls of the salón and the baño came out doubled and
smeared, and obstacles turned into diagonal streaks. Navigation still worked,
but the map on screen was no longer the map that had been saved.

Measured, the doubling was not random drift. Two scans taken with the robot
stopped were matched against the saved map and against Gazebo's ground truth:

| Robot stopped in | Pose that fits the saved map | Ground truth | Difference |
|---|---|---|---|
| salón | (7.65, 3.55), 128.6° | (7.34, 3.68), 127.1° | **+0.31 m in x**, 1.5° |
| by the entrance | (3.77, 0.25), 128.6° | (3.45, 0.18), 127.1° | **+0.32 m in x**, 1.5° |

The saved map was internally consistent (the scan fitted it perfectly) but
shifted with respect to the world past the house's long hall — a slip while it
was driven by hand ([ADR-036](ADR-036-nav2-robot-radius-from-the-model.md) has
that story). Mapping mode then drew the *true* geometry on top of the shifted
walls, which is what "doubled walls" is.

A map that changes whenever it is used defeats the reason to save one: a take
cannot be repeated from the same starting point, a run cannot be reproduced, and
the memories written on that map describe a map that no longer exists.

**SLAM Toolbox's own localization mode was tried first and rejected.** With
`mode: localization` and a 3-scan rolling buffer, it lost the robot: after a
room tour its pose was 0.79 m from where the scan fitted the map (6% of scan
endpoints within 10 cm, against 100% at the fitting pose), and only one of four
rooms was reached. Two further behaviours of that mode were found, both
confirmed live on SLAM Toolbox 2.8.5:

- it **rejects the dock start** ("Starting localization at first node (dock) is
  correctly not supported"), so it needs `map_start_pose`;
- it **refuses to serialize and still reports success**: the node logs "Cannot
  call serialize map in localization mode!", answers `result=0`
  (`RESULT_SUCCESS`) and writes no file. `save_map` only checked that a response
  arrived, so "Guardar mapa" would have confirmed a save that never happened.

## Decision

1. **`saved_map_mode` launch argument** on `simulation`, `full_system` and
   `demo`, with two values:
   - **`mapping` (default)** — ADR-026's behaviour: the pose graph goes into
     SLAM Toolbox with a dock start and keeps growing. The saved file on disk is
     still only written by an explicit save.
   - **`localization`** — `simulation.launch.py` includes `nav2_bringup`'s
     `localization_launch.py` instead of SLAM: **map_server** serves the saved
     occupancy image and **AMCL** localizes on it from the map origin
     (`set_initial_pose: true` in `nav2_params.yaml`, `laser_max_range`
     corrected to the LDS-01's 3.5 m). Nothing writes the map.
   Without a saved map nothing changes: SLAM maps from scratch (ADR-004).
2. **`localization` is the default.** A demo or a benchmark on a saved map
   wants the map that was saved. It reached that state in two steps: read-only
   first navigated 0 of 4 rooms, and the cause was not the mode (below).
3. **`start_zone:=<name>` starts the robot at a named zone's centre**, spawning
   it there in the world and telling SLAM (`map_start_pose`) or AMCL
   (`initial_pose`) where that is. `demo.launch.py` uses `entrada`. Without it a
   run starts wherever mapping began — in this house a nook 0.25 m from a wall,
   which no plan could leave once the costmap knew the robot's real size
   (ADR-036). An unknown zone name fails the launch with the stored names.
4. **An unknown mode fails the launch**, like an unknown map id.
5. **`save_map` writes the occupancy image too** (`map.yaml` + `map.pgm`, via
   SLAM Toolbox's map saver) next to the pose graph, since that is what
   map_server loads, **and verifies both files were written**: it compares their
   modification stamps before and after each call
   (`robot_rag.map_session.saved_map_stamp` / `map_image_stamp`) instead of
   trusting the result code. A save that wrote nothing raises an error naming
   the mode; an empty map folder it created is removed. A map saved before this
   change has no image, and opening it read-only fails with the command that
   writes one.
6. **The localizer is found by name, not assumed.** `nav_skill` looks for
   `amcl` or `slam_toolbox` in the graph and waits for that lifecycle node
   (`robot_skills/localization.py`). SimpleCommander's own AMCL wait is not
   used: it publishes an initial pose at the origin until AMCL answers, which
   would throw away the pose of a robot that had already been driven.

## Rationale

**Why offer read-only at all.** A map that changes while it is used cannot be
recorded twice, and the intent — "the map a run starts from is the map that was
saved" — is right. The parameter makes it available and makes the comparison
repeatable; a better map is what it waits for.

**Why read-only is the default, after all.** The first measurements said the
opposite — 4 of 4 rooms in mapping mode against 1 of 4 read-only — and the
reason turned out to be the *start pose*, not the map: every run began in the
spawn nook, where the planner could not produce a path at any robot radius
(tested down to 0.14 m). Starting in the `entrada` zone instead, read-only
drove all three of the demo's goals, twice. With the map frozen, a take cannot
change the map the next take starts from, which is the property the demo
needs.

**Why AMCL and not SLAM Toolbox's localization mode** for the read-only path.
Measured above: that mode lost the robot by 0.79 m on a tour, and reached one
room of four. AMCL fuses odometry with a motion model instead of relying on a
3-scan buffer, and it never writes the map. ADR-004's reason for having no
AMCL — "this project maps live, so there is no static map" — does not apply once
there is one.

**Not done — make the map accurate enough for read-only to win.** That is the
real fix: a mapping run without a slip (ADR-036), or a loop-closure pass over
the saved graph, and then the same tour again in both modes. It is the
follow-up this ADR exists to make cheap — the parameter is already there.

**Why check the files and not the result code.** The result code is what failed:
SLAM Toolbox reports success for a refused save. The file on disk is what the
user asked for.

## Consequences

Measured on 2026-09-17, on two hand-made maps of the same house, one tour each
(navigate to the four named rooms, `scan_360` in each), ground truth read from
Gazebo at every stop:

| Load mode, starting where the map begins | Rooms reached | Localization error vs ground truth | Map after the tour |
|---|---|---|---|
| `mapping` (SLAM continues the graph) | **4 / 4** | — | walls doubled where the map disagreed with the world |
| `localization` (map_server + AMCL), first map | 0 / 4 | 0.20 m, 13° after a slip | unchanged |
| `localization`, remapped map | 1 / 4 | 0.41–0.44 m, < 2° | unchanged |
| SLAM Toolbox's own localization mode | 1 / 4 | 0.79 m | unchanged |

Then the same map read-only, `start_zone:=entrada`, driving the three goals the
demo records (navigate + perceive, door to door, two runs — the second after
[ADR-037](ADR-037-pay-for-the-real-time-factor.md) sped the simulation up):

| Goal | From the nook | From `entrada` | After ADR-037 |
|---|---|---|---|
| "Ve a estacion_b" | no plan | arrived, 70 s | arrived, **34 s** |
| "Llévame a donde había muchos objetos" | no plan | arrived, 9 s | arrived, 9 s |
| "Ve donde se suele cocinar" | no plan | arrived, 37 s | arrived, **27 s** |

- **The spawn nook was the problem, and the map distortion is the next one.**
  From the nook the planner refused every goal; from `entrada` the same map,
  the same mode and the same code drove all three. The distortion below still
  costs accuracy — the robot arrives 0.4 m from where the map says — but it no
  longer prevents navigation.
- **AMCL was not the problem either; the maps are.** At the pose where the tour
  stopped, the scan fitted the saved map perfectly 0.47 m away from the robot's
  true position, and AMCL was within 0.11 m of that fitting pose. Both maps
  carry that kind of offset (0.31 m and 0.47 m measured in different rooms), so
  a static costmap built from them puts doorways where they are not.
- Verified live otherwise: a saved map loads read-only at its saved size
  (301 × 222 cells, 113.5 m² known), AMCL sets the initial pose at the origin,
  the zones are re-indexed into the pinned session, and Nav2 comes up active.
- On the earlier, shifted map, "Guardar mapa" returned the read-only error with
  the map untouched and no folder left behind, and SLAM Toolbox logged its
  refusal — the case that used to report success.
- **In read-only mode the map cannot be saved**, and "Guardar mapa" says so
  instead of reporting a success: there is no SLAM to serialize. To change a
  map, load it in mapping mode, drive the area and save it under the same id.
- **A saved map must be metrically accurate to be useful read-only**, and a map
  driven by hand in this simulator is not, yet. Measuring it is now part of the
  job: fit a stopped scan against `map.pgm` and compare with Gazebo's pose.
  Making it accurate — a mapping run without slips, or a loop-closure pass — is
  the follow-up that would let the default flip.
- **The occupancy image is a second artefact per map.** `data/maps/<id>/` holds
  `map.posegraph`, `map.data`, `map.yaml` and `map.pgm`; the shipped-map path
  (`robot_bringup/maps/<id>/`) needs all four.
- The spawn-pose assumption is unchanged from ADR-026: a saved map must be
  loaded with the robot spawning where mapping began.
- Tests: `tests/test_saved_maps.py` (read-only by default, mapping mode's dock
  start, invalid modes, finding the occupancy image through its yaml, the error
  when it is missing), `tests/test_map_session.py` (both stamps: they need their
  files and move only when the files are rewritten) and
  `tests/test_localization.py` (which localizer a graph implies).
