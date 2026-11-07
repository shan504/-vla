"""Exercise ROS client stop paths with a transport-only ROS shim; no hardware."""

import sys
import time
import types

import numpy as np
import pytest
import requests

from go2 import robot_client


@pytest.fixture
def ros_shim(monkeypatch):
    published = []
    class Twist:
        def __init__(self):
            self.linear = types.SimpleNamespace(x=0.0, y=0.0)
            self.angular = types.SimpleNamespace(z=0.0)
    class Node:
        def __init__(self, *args):
            pass
        def create_publisher(self, *args):
            return types.SimpleNamespace(publish=lambda msg: published.append((msg.linear.x, msg.angular.z)))
        def create_subscription(self, *args):
            pass
        def create_timer(self, *args):
            pass
        def get_logger(self):
            return types.SimpleNamespace(info=lambda *args: None, error=lambda *args: None)
        def destroy_node(self):
            pass
    class Sync:
        def __init__(self, *args):
            pass
        def registerCallback(self, *args):
            pass
    class Thread:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def join(self, *args, **kwargs):
            pass
    class PID:
        def __init__(self, **kwargs):
            pass
        def solve(self, *args):
            return 0.2, 0.1, 0, 0
    def mod(name, **values):
        module = types.ModuleType(name)
        for key, value in values.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module
    rclpy = mod('rclpy', init=lambda **kwargs: None, ok=lambda: False, shutdown=lambda: None)
    mod('rclpy.node', Node=Node)
    mod('rclpy.qos', qos_profile_sensor_data=object())
    mod('cv_bridge', CvBridge=type('CvBridge', (), {}))
    mod('geometry_msgs.msg', Twist=Twist)
    mod('nav_msgs.msg', Odometry=object)
    mod('sensor_msgs.msg', CameraInfo=object, Image=object)
    mod('message_filters', Subscriber=lambda *args, **kwargs: object(), ApproximateTimeSynchronizer=Sync)
    mod('controllers', Mpc_controller=object, PID_controller=PID)
    monkeypatch.setattr(robot_client.threading, 'Thread', Thread)
    return rclpy, published


def test_ros_client_stop_clears_previous_goal_and_velocity(ros_shim):
    rclpy, published = ros_shim
    def spin(node):
        node.odom = np.zeros(3)
        node.odom_time = time.monotonic()
        node.observation = (b'rgb', b'depth', 0, time.monotonic())
        node.apply_prediction({'discrete_action': [1]}, node.odom)
        node.control()
        assert published[-1] == (0.2, 0.1)
        node.apply_prediction({'discrete_action': [0]}, node.odom)
        assert node.completed
        assert node.goal is node.kind is node.controller is None
        node.control()
        assert published[-1] == (0.0, 0.0)
    rclpy.spin = spin
    robot_client.main(['--instruction', 'Walk to the door'])


def test_request_failure_stops_active_plan(ros_shim, monkeypatch):
    rclpy, published = ros_shim
    def fail(*args, **kwargs):
        raise requests.ConnectionError('offline')
    monkeypatch.setattr(requests, 'post', fail)
    def spin(node):
        node.odom = np.zeros(3)
        node.odom_time = time.monotonic()
        node.odom_queue.append((0.0, node.odom))
        node.observation = (b'rgb', b'depth', 0.0, time.monotonic())
        node.intrinsic = np.eye(3).tolist()
        node.apply_prediction({'discrete_action': [1]}, node.odom)
        node.control()
        assert published[-1][0] > 0
        calls = [0]
        def wait(timeout):
            calls[0] += 1
            return calls[0] > 2
        monkeypatch.setattr(node.stop_event, 'wait', wait)
        node.plan()
        assert not node.lease.active()
        assert node.goal is node.controller is node.kind is None
        assert published[-1] == (0.0, 0.0)
    rclpy.spin = spin
    robot_client.main(['--instruction', 'Walk to the door'])


def test_dry_run_never_creates_or_uses_velocity_publisher(ros_shim):
    rclpy, published = ros_shim
    def spin(node):
        assert node.command_pub is None
        node.publish(0.2, 0.1)
        node.clear()
    rclpy.spin = spin
    robot_client.main(['--instruction', 'Walk to the door', '--dry-run'])
    assert not published
