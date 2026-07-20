## What the robot can perceive

Perception is classical, not learned: the robot has no object detector and no
vision-language model. `perceive` and `scan_360` report exactly two things,
derived from sensor statistics:

- **Dominant colours** of the camera frame, as colour names (white, brown,
  grey, green, ...).
- **How cluttered or open the space is**, derived from the LIDAR scan: the
  number of nearby obstacle groups and the free distance around the robot.

An earlier version used a vision-language model to name objects. It was
removed because it was unreliable on this simulator's software-rendered frames
(see ADR-014). Any statement about a specific object being present is therefore
outside what the robot can currently observe.

## What the robot cannot perceive

The robot **cannot** name, count, or locate individual objects — no chairs, no
tables, no bottles, no doors. It cannot read text, recognise people, or judge
whether a door is open or closed.

If a goal requires naming objects ("tell me if there is a chair here"), the
honest answer is that the robot can only describe the place's colours and how
open or cluttered it is. Never claim to have seen a specific object.

## How a place is described

A stored description looks like this:

    predominantly white, an open, uncluttered space

or:

    predominantly brown, a cluttered space with many objects (9 obstacle
    groups nearby)

These descriptions are stored in the semantic map together with the
coordinates where they were observed, which is what makes a goal like "go to
the white, open room" resolvable later.

## Which skill to use

- `perceive` — a single snapshot from the current heading. Fast; the camera's
  field of view is narrow, so it sees only what is in front of the robot.
- `scan_360` — rotates in place and samples the camera at each heading, then
  merges the samples into one panoramic description. Slower, but far better
  colour coverage. Use it for "look all around you" style goals.

Both store their result in the semantic map, keyed to the robot's position.
