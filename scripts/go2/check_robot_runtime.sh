#!/usr/bin/env bash
# Inspect installed tooling only. No ROS launch, DDS initialization, robot
# service switches, package installation, network changes or movement commands.
set -u
if [[ $# -gt 0 ]]; then
  if [[ $# -eq 1 && $1 == --help ]]; then
    echo "Usage: bash scripts/go2/check_robot_runtime.sh"
    echo "Read installed SDK/ROS/tool paths and process names; make no changes."
    exit 0
  fi
  echo "Usage: bash scripts/go2/check_robot_runtime.sh" >&2
  exit 2
fi

printf 'Architecture: '
uname -m
if [[ -r /proc/device-tree/model ]]; then
  printf 'Board: '
  tr -d '\0' < /proc/device-tree/model
  printf '\n'
fi
if [[ -r /etc/nv_tegra_release ]]; then head -n 1 /etc/nv_tegra_release; fi

printf '\nInstalled tools:\n'
for robot_tool in cmake g++ make pkg-config ros2; do
  if robot_tool_path="$(command -v "$robot_tool" 2>/dev/null)"; then
    printf '%s: %s\n' "$robot_tool" "$robot_tool_path"
  else
    printf '%s: not on PATH\n' "$robot_tool"
  fi
done
if command -v g++ >/dev/null 2>&1; then g++ -dumpfullversion; fi

printf '\nROS setup files (not sourced):\n'
robot_found_ros=0
for robot_setup in /opt/ros/*/setup.bash; do
  if [[ -f "$robot_setup" ]]; then
    printf '%s\n' "$robot_setup"
    robot_found_ros=1
  fi
done
if [[ $robot_found_ros == 0 ]]; then echo "No /opt/ros/*/setup.bash found"; fi

printf '\nCommon SDK/workspace paths:\n'
robot_found_sdk=0
for robot_sdk_path in \
  "$HOME"/unitree_sdk2 "$HOME"/unitree_sdk2_python \
  "$HOME"/ros2_ws "$HOME"/unitree_ws "$HOME"/go2_ws \
  "$HOME"/jqdy/unitree_sdk2 /opt/unitree_robotics; do
  if [[ -d "$robot_sdk_path" ]]; then
    printf '%s\n' "$robot_sdk_path"
    robot_found_sdk=1
  fi
done
if [[ $robot_found_sdk == 0 ]]; then echo "None of the common SDK/workspace paths found"; fi

printf '\nDDS libraries in loader cache:\n'
if command -v ldconfig >/dev/null 2>&1; then
  ldconfig -p 2>/dev/null | awk '/libddsc(xx)?\./ {print}'
fi
printf '\nROS, DDS and robot process names (arguments omitted):\n'
ps -eo pid=,comm= | awk 'tolower($2) ~ /(ros|dds|sport|slam|odom|motion)/ {print}'
