#!/bin/bash
# Source this file to prepare a shell for running or building robot_ws nodes.
#
# ROS 2 nodes are built and executed with the system python3 (matching rclpy's
# interpreter), while third-party deps (chromadb, ollama, fastapi, ...) live in
# agent_env. PYTHONPATH bridges the two instead of activating the venv, since
# activating agent_env would swap python3 for colcon/ros2 and break the
# console_scripts shebang generated at build time. See docs/decisions/ADR-003.
#
# The workspace root is derived from this script's own location, so a clone in
# any directory works without editing anything. ROBOT_WS is what the nodes read
# to build their default data paths.

_SETUP_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source /opt/ros/jazzy/setup.bash
if [ -f "${_SETUP_ENV_DIR}/install/setup.bash" ]; then
    source "${_SETUP_ENV_DIR}/install/setup.bash"
fi

export ROBOT_WS="${_SETUP_ENV_DIR}"
export PYTHONPATH="${ROBOT_WS}/agent_env/lib/python3.12/site-packages:${PYTHONPATH}"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file://${ROBOT_WS}/cyclonedds.xml"
export ROS_DOMAIN_ID=0
export TURTLEBOT3_MODEL=waffle
export OLLAMA_KEEP_ALIVE=-1
export CHROMA_DB_PATH="${ROBOT_WS}/data/chroma_db"
export KNOWLEDGE_DIR="${ROBOT_WS}/data/knowledge"

unset _SETUP_ENV_DIR
