# ADR-021: Container for build and verification, simulator left on the host

**Date:** 2026-09-15
**Status:** Accepted

## Context

Every claim this project makes about itself — 120 pure-logic tests, 21 node
tests, a lint-clean tree, a reproducible ablation benchmark — was, until now,
verifiable only by first reproducing the author's environment: ROS 2 Jazzy,
Gazebo Harmonic, a CUDA-capable Ollama, a WSL2 Ubuntu 24.04 with a venv bridged
through `PYTHONPATH` (ADR-003). That is a long setup to ask of a reader whose
actual question is "does this work?".

The [pre-publication review](../REVIEW.md) already caught the failure mode this
creates: the project had **never been run by anyone other than its author**, and
the defects that surfaced were almost all of that single kind — hardcoded home
directories, a `colcon test` nobody had run, session state leaking between
benchmark conditions. The fresh-clone check in a `ros:jazzy-ros-base` container
is what caught them, and it was run by hand, once, and then not kept.

So the decision is not "should the project have a Dockerfile" — the container
had already proved its worth as a verification tool. It is what belongs inside
it, because the obvious answer (everything) is wrong.

## Decision

Ship a `Dockerfile` and a `.devcontainer/` that cover **everything not needing a
simulator or a GPU**: the workspace build, `ruff`, both test layers (ADR-018),
and the offline benchmark (ADR-020).

Gazebo, RViz, Nav2 end-to-end navigation and live SLAM stay on the host and are
explicitly out of scope. The image says so, the entrypoint prints it on every
run, and the README repeats it.

Concretely:

- **Base `ros:jazzy-ros-base`, apt dependencies mirroring the ROS job in CI** —
  the configuration these tests are known green under — plus
  `ros-jazzy-rmw-cyclonedds-cpp`, so the project's own `setup_env.sh` runs
  unmodified inside the container (ADR-006). The pip side deliberately does
  *not* mirror CI: that job installs only what the nodes import at module load,
  whereas this image also runs layer 1 and the offline benchmark, so it takes
  the full `requirements.txt`. That difference is what surfaced the one real
  packaging problem here — `chromadb` pulls a newer `pyyaml`, which pip cannot
  install because it cannot uninstall the apt-owned 6.0.1 underneath it. Fixed
  by installing PyYAML first with `--ignore-installed`, into the pip
  dist-packages that precedes apt's on `sys.path`. `numpy` is pointedly not
  handled that way: it has to keep matching the ABI of the apt `cv_bridge`.
- **The entrypoint sources `setup_env.sh`**, the same script a host developer
  sources. The container cannot drift from the documented environment because
  it does not have its own copy of it.
- **`ROBOT_WS=/robot_ws`** — the image lives at a path that is not the author's
  home directory, with no code change, which is a standing regression test on
  ADR-015 rather than a one-off check.
- **No venv.** ADR-003's `PYTHONPATH` bridge exists because activating a venv
  swaps the `python3` that `colcon` and the generated console-script shebangs
  expect. In a container the system Python *is* the project Python, so the
  problem does not arise and reproducing the bridge would only add a moving
  part.
- **The devcontainer keeps `build/` and `install/` in named volumes**, not in
  the bind mount. `colcon --symlink-install` fills them with absolute paths into
  whichever workspace produced them, so a shared copy means a container build
  silently corrupts the host's, and vice versa.
- **`--network=host`** in the devcontainer: Ollama already listens on
  `localhost:11434` in the WSL2 VM, so `ollama_base_url` resolves with no
  override and the benchmark runs unchanged.

## Rationale

**Why not put Gazebo in the image.** It is technically possible and it would
read better in a README. It is also the dishonest option. Gazebo here already
renders on `llvmpipe` because WSL2's GPU passthrough is incomplete; adding a
container layer and an X socket makes a slow, software-rendered simulation
slower and more fragile, and the resulting image would invite a reader to run
the *one* part of this project that the documentation is careful to say is
unreliable (`REVIEW.md` §6.2). An image whose advertised capability fails on
first contact is worse than no image. The boundary is drawn where the honest
claim is: everything deterministic is in, everything needing rendering is out.

**Why mirror CI rather than the host.** The host is WSL2 with a venv bridge and
a GPU; CI is the minimal environment in which the tests are actually known to
pass. Mirroring CI means the image is a *third* independent confirmation of the
same green result rather than a second copy of the author's machine — which is
the entire point, given the class of defect the review found.

**Alternatives rejected:**

- *`docker compose` with an Ollama service.* Would make the benchmark one
  command, but pulls a 5 GB model into a build, and the GPU passthrough needed
  to make it anything but unusably slow is exactly the fragile part. The host
  Ollama is already running; reaching it is a network flag.
- *A devcontainer only, no plain Dockerfile.* Ties verification to VS Code. The
  reader most worth convincing runs `docker run` and reads the exit code.
- *Reproducing the `agent_env` venv inside the image.* Faithful to the host,
  pointless in a container, and one more thing to keep in sync.

## Consequences

- `docker build -t robot-rag-agent . && docker run --rm robot-rag-agent` is now
  the shortest complete answer to "does this actually work?", and it needs
  neither ROS 2 nor Python nor a GPU on the reader's machine.
- The README's "works on my WSL2" caveat narrows from the whole project to the
  simulator alone, which is where it was always true.
- **New sync obligation:** the Dockerfile's dependency list and CI's ROS job now
  state the same thing twice. They are cross-referenced by comment in both
  files; a dependency added to one must be added to the other. A single source
  (CI building from the Dockerfile) was considered and deferred — it slows the
  fast no-ROS job down to an image build for no gain today.
- The image does not exercise `rag_node` (it ingests against a live Ollama on
  startup) or any skill that needs the simulator. Those keep the coverage they
  had: manual, and documented as manual (ADR-018 layer 3).
