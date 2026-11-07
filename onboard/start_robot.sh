#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROBOT_IF="${ROBOT_IF:-eth0}"
ROBOT_PYTHON="${ROBOT_PYTHON:-python3}"
ROS_DISTRO="${ROS_DISTRO:-foxy}"
# source ROS outside a Conda environment; ROS packages use system Python.
if [[ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
    set +u
    source "/opt/ros/${ROS_DISTRO}/setup.bash"
    set -u
fi
children=()
cleanup() {
    trap - EXIT INT TERM
    if ((${#children[@]})); then
        kill -TERM "${children[@]}" 2>/dev/null || true
        wait "${children[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$ROBOT_PYTHON" unitree_driver.py "$ROBOT_IF" &
children+=("$!")
"$ROBOT_PYTHON" ros_bridge.py &
children+=("$!")
"$ROBOT_PYTHON" realsense_publisher.py &
children+=("$!")
echo "Go2 drivers started on ${ROBOT_IF}; no navigation task started. Ctrl+C stops owned processes."
# A failed child tears down the remaining processes rather than leaving a driver running.
wait -n "${children[@]}"
exit 1
