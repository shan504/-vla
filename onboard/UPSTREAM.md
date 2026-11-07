# Go2 edge driver provenance

The initial `start_robot.sh`, `unitree_driver.py`, `ros_bridge.py`, and
`realsense_publisher.py` were imported from
[cmjang/InternNav-deploy](https://github.com/cmjang/InternNav-deploy), commit
`7e00007ece25feee35cb0ca04c47ef5ce443be27` (MIT; see `LICENSE`).

Local integration changes: configurable network interface and system Python;
shutdown only owned child processes; no automatic stand-up; finite UDP command
watchdog and velocity limits; discard stale commands during remote override;
finite-value checks; odometry sensor QoS; RealSense unit normalization.

Start these only on your Go2-connected computer. The SDK, ROS 2 and USB camera
must be installed there. These scripts are not started by the cloud install.
