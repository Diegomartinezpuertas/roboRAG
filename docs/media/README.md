# docs/media

Visual material referenced from the root README. Committed on purpose (unlike
`docs/promo/`, which is local-only working material): these files are part of
what the repository shows a reader.

Expected files, and what each one has to prove:

| File | Shot | What it must show |
|---|---|---|
| `demo.gif` | Dashboard, 15–20 s | A goal typed in → the timeline filling with the LLM's retrieval and plan → the robot moving on the map. The whole loop in one clip. Use a *functional* goal ("ve donde se suele cocinar") once a room has been named: it proves more than "go to the kitchen" and costs the same frames. |
| `dashboard.png` | Dashboard, still | The whole window: live SLAM map with the robot and a named zone, a *completed* timeline (retrieved context, plan steps, final response), and the memory panel populated. One frame that shows the system thinking and remembering. |
| `memory.png` | Dashboard, still, memory panel | A functional query typed into the memory panel and the cards it returns — similarity bars, coordinates, zone — with the matching markers visible on the map. This is the differentiator made visible: the memory is inspectable, not a black box. |
| `rviz.png` | RViz, still | Robot in the map with LIDAR points and Nav2 costmaps. This is the frame that reads as "real robotics" rather than "a web app". |

Keep `demo.gif` under ~10 MB or GitHub will be slow to render it.

**Getting a map to film on:** map the house once by hand — launch with
`use_nav2:=false`, turn on manual driving in the dashboard, drive with WASD
([ADR-023](../decisions/ADR-023-browser-teleop.md)), name each room with
"Nombrar esta habitación" (that is what makes the functional goal in `demo.gif`
resolve) and save it with "Guardar mapa" as **`house`**. From then on every take
starts from it: `ros2 launch robot_bringup demo.launch.py` opens Gazebo, RViz and
the dashboard on that map, with the memory session pinned to it
([ADR-026](../decisions/ADR-026-shipped-map-and-demo-launch.md)). Autonomous
exploration is not a substitute here: even with frontier clusters it maps about
14 m² in four minutes, a room or two rather than the house
([ADR-027](../decisions/ADR-027-exploration-frontier-clusters.md)).

## Paste-ready README section

Once the files exist, this goes immediately after the badges in the root
`README.md`, before "Does the RAG actually help?":

```markdown
## Demo

![The agent taking a natural-language goal and executing it](docs/media/demo.gif)

A goal in natural language, the retrieval and plan traced step by step in the
dashboard, and the robot executing it under Nav2 — all local, all on one laptop.

| Dashboard — live SLAM map, zones, and the planner's reasoning | RViz — LIDAR and Nav2 costmaps |
|---|---|
| ![Dashboard](docs/media/dashboard.png) | ![RViz](docs/media/rviz.png) |

The memory is not a black box: ask it a question and see what the robot would
retrieve, with its score and the spot on the map where it learned it.

![The RAG memory panel answering a functional query](docs/media/memory.png)
```
