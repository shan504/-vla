#!/usr/bin/env python3
"""
File B: ros_bridge.py
Role: ROS Node Hub.
1. Subscribes to /cmd_vel_bridge -> Forwards via UDP to robot driver.
2. Subscribes to /utlidar/robot_odom -> Relays to /odom_bridge.
"""
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry  # Added for Odom handling
import socket
import struct
import argparse
import time

from native_odom import parse_native_state, quaternion_from_rpy

# Target Address (Must match unitree_driver.py)
TARGET_IP = "127.0.0.1"
TARGET_PORT = 8899

class RosBridge(Node):
    def __init__(self, odom_source='lidar', state_port=8898):
        super().__init__('unitree_ros_bridge')

        # === Part 1: Downstream (ROS -> UDP) ===
        # Create UDP Socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Subscribe to velocity command
        self.create_subscription(Twist, '/cmd_vel_bridge', self.cmd_callback, 10)

        # === Part 2: Upstream (ROS -> ROS Relay) ===
        # Publisher for the new topic
        self.odom_pub = self.create_publisher(Odometry, '/odom_bridge', 10)

        # Subscribe to the original LiDAR odom topic
        # BEST_EFFORT subscription also accepts reliable publishers.
        self.state_sock = None
        self.last_state_ns = 0
        if odom_source == 'sdk':
            self.state_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.state_sock.bind(('127.0.0.1', state_port))
            self.state_sock.setblocking(False)
            self.create_timer(0.05, self.sdk_odom_callback)
        else:
            self.create_subscription(Odometry, '/utlidar/robot_odom', self.odom_callback, qos_profile_sensor_data)

        self.get_logger().info(f"ROS Bridge Started.")
        self.get_logger().info(f" -> Forwarding /cmd_vel_bridge to UDP {TARGET_IP}:{TARGET_PORT}")
        self.get_logger().info(' -> Odometry source: ' + odom_source + '; output: /odom_bridge')

    def sdk_odom_callback(self):
        latest = None
        for _ in range(64):
            try:
                data, _ = self.state_sock.recvfrom(4097)
            except BlockingIOError:
                break
            try:
                state, _ = parse_native_state(data)
                if state['receive_monotonic_ns'] > self.last_state_ns:
                    if latest is None or state['receive_monotonic_ns'] > latest['receive_monotonic_ns']:
                        latest = state
            except (ValueError, TypeError, UnicodeError):
                continue
        if latest is None:
            return
        # Recheck age after draining; this is local SDK receive time, not a
        # claim about the robot sensor's hardware clock.
        now_ns = time.monotonic_ns()
        age_ns = now_ns - latest['receive_monotonic_ns']
        if not 0 <= age_ns <= 500_000_000:
            return
        self.last_state_ns = latest['receive_monotonic_ns']
        stamp_ns = self.get_clock().now().nanoseconds - age_ns
        if stamp_ns < 0:
            return
        msg = Odometry()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp_ns, 1_000_000_000)
        msg.header.frame_id, msg.child_frame_id = 'sdk_odom', 'base_link'
        msg.pose.pose.position.x, msg.pose.pose.position.y, msg.pose.pose.position.z = map(float, latest['position'])
        q = quaternion_from_rpy(latest['rpy'])
        msg.pose.pose.orientation.x, msg.pose.pose.orientation.y = q[:2]
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = q[2:]
        # SDK velocity frame/covariance is not assumed here; the existing
        # InternNav follower uses only pose. Mark unprovided covariance unknown.
        for index in (0, 7, 14, 21, 28, 35):
            msg.pose.covariance[index] = 1e6
            msg.twist.covariance[index] = 1e6
        self.odom_pub.publish(msg)

    def cmd_callback(self, msg):
        """
        Handles velocity commands from ROS and sends to Unitree Driver via UDP.
        """
        vx = float(msg.linear.x)
        vyaw = float(msg.angular.z)

        try:
            # Pack data into binary (2 floats)
            data = struct.pack('<ff', vx, vyaw)
            self.sock.sendto(data, (TARGET_IP, TARGET_PORT))
        except Exception as e:
            self.get_logger().error(f"Failed to send UDP command: {e}")

    def odom_callback(self, msg):
        """
        Handles Odometry messages and republishes them to the new topic.
        """
        # We can modify the message here if needed (e.g., change frame_id)
        # For now, we just pass it through directly.
        self.odom_pub.publish(msg)

def main(args=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--odom-source', choices=['lidar', 'sdk'], default='lidar')
    parser.add_argument('--state-port', type=int, default=8898)
    options, ros_args = parser.parse_known_args(args)
    if not 1 <= options.state_port <= 65535:
        parser.error('--state-port must be in 1..65535')
    rclpy.init(args=ros_args)
    node = RosBridge(options.odom_source, options.state_port)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.sock.sendto(struct.pack('<ff', 0.0, 0.0), (TARGET_IP, TARGET_PORT))
        node.sock.close()
        if node.state_sock is not None:
            node.state_sock.close()
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
