# ADR-035: Loading a saved map read-only — offered, measured, not the default

**Date:** 2026-09-17
**Status:** Accepted — `saved_map_mode` added; the default stays ADR-026's
mapping, because read-only navigated worse on the maps this project produces

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
2. **The default stays `mapping`, on the measurement below**, not on principle:
   read-only is the better idea and the worse behaviour on a map with 0.3–0.5 m
   of distortion, which is what mapping this house by hand produces.
3. **An unknown mode fails the launch**, like an unknown map id.
4. **`save_map` writes the occupancy image too** (`map.yaml` + `map.pgm`, via
   SLAM Toolbox's map saver) next to the pose graph, since that is what
   map_server loads, **and verifies both files were written**: it compares their
   modification stamps before and after each call
   (`robot_rag.map_session.saved_map_stamp` / `map_image_stamp`) instead of
   trusting the result code. A save that wrote nothing raises an error naming
   the mode; an empty map folder it created is removed. A map saved before this
   change has no image, and opening it read-only fails with the command that
   writes one.
5. **The localizer is found by name, not assumed.** `nav_skill` looks for
   `amcl` or `slam_toolbox` in the graph and waits for that lifecycle node
   (`robot_skills/localization.py`). SimpleCommander's own AMCL wait is not
   used: it publishes an initial pose at the origin until AMCL answers, which
   would throw away the pose of a robot that had already been driven.

## Rationale

**Why offer read-only at all.** A map that changes while it is used cannot be
recorded twice, and the intent — "the map a run starts from is the map that was
saved" — is right. The parameter makes it available and makes the comparison
repeatable; a better map is what it waits for.

**Why mapping stays the default.** Measured: 4 of 4 rooms reached in mapping
mode, 1 of 4 read-only on the same house. With the map 0.4 m out in places,
Nav2's static costmap disagrees with the world about where the doorways are,
while a mapping SLAM redraws them where the LIDAR sees them.

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

| Load mode | Rooms reached | Localization error vs ground truth | Map after the tour |
|---|---|---|---|
| `mapping` (SLAM continues the graph) | **4 / 4** | — | walls doubled where the map disagreed with the world |
| `localization` (map_server + AMCL), first map | 0 / 4 | 0.20 m, 13° after a slip | unchanged |
| `localization`, remapped map | 1 / 4 | 0.41–0.44 m, < 2° | unchanged |
| SLAM Toolbox's own localization mode | 1 / 4 | 0.79 m | unchanged |

- **AMCL was not the problem; the maps are.** At the pose where the tour
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
