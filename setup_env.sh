#!/bin/bash
# Source this file to prepare a shell for running or building robot_ws nodes.
#
# ROS 2 nodes are built and executed with the system python3 (matching rclpy's
# interpreter), while third-party deps (chromadb, langchain, ollama, ...) live
# in agent_env. PYTHONPATH bridges the two instead of activating the venv,
# since activating agent_env would swap python3 for colcon/ros2 and break the
# console_scripts shebang generated at build time. See docs/decisions/ADR-003.

source /opt/ros/jazzy/setup.bash
if [ -f /home/diego/robot_ws/install/setup.bash ]; then
    source /home/diego/robot_ws/install/setup.bash
fi

export PYTHONPATH="/home/diego/robot_ws/agent_env/lib/python3.12/site-packages:${PYTHONPATH}"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export CYCLONEDDS_URI="file:///home/diego/robot_ws/cyclonedds.xml"
export ROS_DOMAIN_ID=0
export TURTLEBOT3_MODEL=waffle
export OLLAMA_KEEP_ALIVE=-1
export ROBOT_WS=/home/diego/robot_ws
export CHROMA_DB_PATH="${ROBOT_WS}/data/chroma_db"
export KNOWLEDGE_DIR="${ROBOT_WS}/data/knowledge"
