## Task templates

Worked goal → plan examples, retrieved as few-shot guidance for the planner.
They use only the real skills (navigate, explore, perceive, scan_360) with
their real parameters, and never a `report` step — the system reports the
outcome automatically after the plan runs.

## Template: go to a known place and describe it

Goal pattern: "Ve a <zone> y dime qué hay" / "Go to <zone> and describe it".
Plan: navigate(zone) → perceive.
(perceive describes the surroundings — dominant colours, how open or cluttered
the space is — and stores the description with coordinates. It does not name
individual objects.)

## Template: explore an area

Goal pattern: "Explora la cocina" / "Explore the kitchen".
Plan: explore(duration_sec, zone). Every place reached while exploring is
described and stored automatically, so a later descriptive goal can resolve
against it.

## Template: look all around from here

Goal pattern: "Gira y dime qué ves" / "Spin around and see what's here".
Plan: scan_360. The robot stays in place, turns through a full circle sampling
the camera at each heading, and stores one panoramic colour description. Use
this — not explore — when the goal is to look around the current spot rather
than travel.

## Template: describe the current location

Goal pattern: "Dime dónde estás" / "Describe your current location".
Plan: perceive.
