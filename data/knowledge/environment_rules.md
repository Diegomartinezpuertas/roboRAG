## Simulated environment layout

The robot operates in the Gazebo `turtlebot3_house` world: a single-floor
house whose geometry is a fixed mesh (furniture is baked into the mesh, not
separate models). It contains a living room, a kitchen/dining area, a
corridor, and bedrooms. The robot spawns at approximately (x=-2.0, y=-0.5)
in the map frame.

## Where locations come from

Location coordinates are NOT hardcoded. The robot learns them two ways:
- Exploration builds the SLAM map incrementally; only mapped areas are
  reachable.
- Named zones (created by the user on the dashboard) and perceived places
  (each described by its dominant colours and how cluttered it is) are stored
  with their map-frame coordinates in semantic memory, and retrieved via RAG.
  If the retrieved context gives coordinates for a target, navigate straight
  to them.

If a requested place has no known coordinates (not a named zone, not in the
retrieved context), the robot must explore to find it rather than guessing a
position.

## Navigation rules

Nav2 plans only through space the LIDAR has mapped as free, and rejects goals
outside the currently mapped boundaries, so unexplored regions must be mapped
first. A plan runs in order and stops at the first step that fails; nothing
retries or replans on its own.
