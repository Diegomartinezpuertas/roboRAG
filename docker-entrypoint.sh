#!/bin/bash
# Entrypoint for the Robot RAG Agent image (ADR-021).
#
# Sources the project's own setup_env.sh — the same script a host developer
# runs — so the container cannot drift from the documented environment. With
# no arguments it runs `verify`: the full offline verification, which is the
# claim the image exists to make good on.
#
# No `set -u`: ROS's setup.bash trips on unbound variables.
set -e

source /robot_ws/setup_env.sh

if [ "$1" = "verify" ]; then
    echo "== ruff =="
    ruff check .

    echo
    echo "== layer 1 — pure logic, no ROS =="
    python3 -m pytest tests/

    echo
    echo "== layer 2 — node level, real executors =="
    colcon test --event-handlers console_direct+
    colcon test-result --all

    echo
    echo "✅ build + lint + both test layers pass in a clean container."
    echo "   Not covered here (needs a simulator or a GPU): Gazebo, Nav2"
    echo "   end-to-end, and the LLM benchmark — see ADR-021."
    exit 0
fi

exec "$@"
