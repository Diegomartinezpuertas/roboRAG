# Reproducible build-and-verify environment for the Robot RAG Agent.
#
# Scope (ADR-021): this image covers everything that does NOT need a simulator
# or a GPU — the workspace build, both test layers, the linter, and the offline
# benchmark (ADR-020). Gazebo, RViz and live navigation stay on the host; they
# need rendering and WSLg, and faking them would make the image a worse claim
# than no image at all.
#
#   docker build -t robot-rag-agent .
#   docker run --rm robot-rag-agent                    # build + lint + both test layers
#   docker run --rm -it robot-rag-agent bash           # poke around
#
# The benchmark additionally needs an Ollama server; see ADR-021 for the
# --network host / OLLAMA_BASE_URL wiring.

FROM ros:jazzy-ros-base

# System dependencies. Mirrors the ROS job in .github/workflows/ci.yml, which
# is the configuration these tests are known green under, plus CycloneDDS so
# the project's own setup_env.sh works unchanged in here (ADR-006).
# ros:jazzy-ros-base ships no pip at all, hence python3-pip.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-colcon-common-extensions \
        python3-opencv \
        python3-pip \
        python3-pytest \
        ros-jazzy-cv-bridge \
        ros-jazzy-nav2-simple-commander \
        ros-jazzy-rmw-cyclonedds-cpp \
        ros-jazzy-rosidl-default-generators \
        ros-jazzy-tf2-ros \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies, copied first so edits to the source do not invalidate
# this layer. --break-system-packages: this is a container, the system Python
# IS the project Python, which is the whole reason ADR-003's venv bridge is
# not reproduced here.
#
# Unlike CI's ROS job, this installs the FULL requirements.txt: that job only
# needs what the nodes import at module load, while this image also runs the
# layer-1 suite and the offline benchmark (chromadb, matplotlib, pyyaml).
#
# PyYAML first, on its own, with --ignore-installed. chromadb pulls kubernetes,
# which pulls a newer pyyaml, and pip cannot uninstall the apt-installed 6.0.1
# to make room ("RECORD file not found") — the whole install aborts. Installing
# it into pip's own dist-packages first (which precedes apt's on sys.path)
# means the resolver finds the requirement already met and never tries.
# numpy is deliberately NOT treated this way: it must keep matching the ABI of
# the apt cv_bridge/cv2, which is why requirements.txt pins numpy<2.
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir --ignore-installed PyYAML \
    && python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Headless Chromium and its system libraries for the dashboard browser tests
# (tests/test_dashboard_ui.py, ADR-030), so layer 1 here runs exactly what CI runs.
RUN python3 -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

# ROBOT_WS is load-bearing (ADR-015): every node derives its data paths from it.
# Setting it here is what lets the image live at a path that is not the
# author's home directory without a single code change.
ENV ROBOT_WS=/robot_ws
WORKDIR /robot_ws
COPY . /robot_ws

RUN . /opt/ros/jazzy/setup.sh && colcon build --symlink-install

# The launch_testing pytest plugin shipped with Jazzy breaks collection on
# current pytest (ADR-018); nothing here uses it.
ENV PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
# Keep DDS discovery inside this container.
ENV ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST

ENTRYPOINT ["/robot_ws/docker-entrypoint.sh"]
CMD ["verify"]
