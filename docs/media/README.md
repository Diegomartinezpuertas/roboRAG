# docs/media

Visual material referenced from the root README. Committed on purpose (unlike
`docs/promo/`, which is local-only working material): these files are part of
what the repository shows a reader.

Expected files, and what each one has to prove:

| File | Shot | What it must show |
|---|---|---|
| `demo.gif` | Dashboard, 15–20 s | A goal typed in → the timeline filling with the LLM's retrieval and plan → the robot moving on the map. The whole loop in one clip. |
| `dashboard.png` | Dashboard, still | Live SLAM map with the robot and a named zone overlay, and a *completed* timeline next to it — retrieved context, plan steps, final response. |
| `rviz.png` | RViz, still | Robot in the map with LIDAR points and Nav2 costmaps. This is the frame that reads as "real robotics" rather than "a web app". |

Keep `demo.gif` under ~10 MB or GitHub will be slow to render it.

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
```
