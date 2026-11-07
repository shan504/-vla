"""ROS 2 Go2 client. Imports and --help never initialize or move the robot."""

import argparse
import io
import json
import math
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .robot_control import CommandLease, clip_command, parse_prediction


def positive_float(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('must be positive and finite')
    return value


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-url', default=os.environ.get('SERVER_URL', 'http://127.0.0.1:5801/eval_dual'))
    instructions = parser.add_mutually_exclusive_group()
    instructions.add_argument('--instruction', default=os.environ.get('INSTRUCTION'))
    instructions.add_argument('--instruction-file', type=Path)
    parser.add_argument('--rgb-topic', default='/camera/camera/color/image_raw')
    parser.add_argument('--depth-topic', default='/camera/camera/aligned_depth_to_color/image_raw')
    parser.add_argument('--camera-info-topic', default='/camera/camera/color/camera_info')
    parser.add_argument('--odom-topic', default='/odom_bridge')
    parser.add_argument('--cmd-topic', default='/cmd_vel_bridge')
    parser.add_argument('--max-v', type=positive_float, default=0.3, help='forward velocity limit, m/s')
    parser.add_argument('--max-w', type=positive_float, default=0.4, help='yaw velocity limit, rad/s')
    parser.add_argument('--plan-timeout', type=positive_float, default=2.0, help='command lifetime after a prediction, seconds')
    parser.add_argument('--sensor-timeout', type=positive_float, default=1.0)
    parser.add_argument('--max-observation-age', type=positive_float, default=5.0, help='discard older RGB-D predictions')
    parser.add_argument('--request-timeout', type=positive_float, default=20.0)
    parser.add_argument('--dry-run', action='store_true', help='perform inference and print velocity; never publish /cmd_vel')
    return parser


def main(argv=None):
    parser = build_parser()
    args, ros_args = parser.parse_known_args(argv)
    if args.instruction_file:
        args.instruction = args.instruction_file.read_text(encoding='utf-8')
    if not args.instruction or not args.instruction.strip():
        parser.error('--instruction, --instruction-file, or INSTRUCTION is required')
    args.instruction = args.instruction.strip()
    if not args.server_url.startswith(('http://', 'https://')):
        parser.error('--server-url must be an HTTP(S) /eval_dual endpoint')

    import numpy as np
    import rclpy
    import requests
    from cv_bridge import CvBridge
    from geometry_msgs.msg import Twist
    from message_filters import ApproximateTimeSynchronizer, Subscriber
    from nav_msgs.msg import Odometry
    from PIL import Image as PILImage
    from rclpy.node import Node
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import CameraInfo, Image

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'realworld'))
    from controllers import Mpc_controller, PID_controller

    class Go2Client(Node):
        def __init__(self):
            super().__init__('internnav_go2_client')
            self.lock = threading.RLock()
            self.stop_event = threading.Event()
            self.completed = False
            self.lease = CommandLease(args.plan_timeout)
            self.controller = self.kind = self.goal = self.odom = None
            self.odom_time = 0.0
            self.odom_queue = deque(maxlen=100)
            self.observation = self.intrinsic = None
            self.image_seq = 0
            self.pid = PID_controller(Kp_trans=2.0, Kd_trans=0.0, Kp_yaw=1.5, Kd_yaw=0.0,
                                      max_v=args.max_v, max_w=args.max_w)
            self.bridge = CvBridge()
            self.command_pub = None if args.dry_run else self.create_publisher(Twist, args.cmd_topic, 5)
            self.rgb_sub = Subscriber(self, Image, args.rgb_topic, qos_profile=qos_profile_sensor_data)
            self.depth_sub = Subscriber(self, Image, args.depth_topic, qos_profile=qos_profile_sensor_data)
            self.sync = ApproximateTimeSynchronizer([self.rgb_sub, self.depth_sub], 5, 0.1)
            self.sync.registerCallback(self.image_callback)
            self.create_subscription(CameraInfo, args.camera_info_topic, self.info_callback, qos_profile_sensor_data)
            self.create_subscription(Odometry, args.odom_topic, self.odom_callback, qos_profile_sensor_data)
            self.timer = self.create_timer(0.1, self.control)
            self.worker = threading.Thread(target=self.plan, daemon=True)
            self.worker.start()
            self.get_logger().info('Waiting for RGB-D, camera calibration and odometry. Instruction: ' + args.instruction)
            if args.dry_run:
                self.get_logger().info('DRY RUN: no command publisher was created.')

        def publish(self, v=0.0, w=0.0):
            v, w = clip_command(float(v), float(w), args.max_v, args.max_w)
            if args.dry_run:
                if v or w:
                    self.get_logger().info('DRY RUN velocity: vx=%.3f, yaw=%.3f' % (v, w))
                return
            msg = Twist()
            msg.linear.x, msg.angular.z = v, w
            self.command_pub.publish(msg)

        def clear(self):
            with self.lock:
                self.lease.clear()
                self.kind = self.goal = self.controller = None
                self.publish()

        def info_callback(self, msg):
            matrix = np.asarray(msg.k, dtype=float).reshape(3, 3)
            if np.isfinite(matrix).all() and matrix[0, 0] > 0 and matrix[1, 1] > 0:
                with self.lock:
                    self.intrinsic = matrix.tolist()

        def image_callback(self, rgb_msg, depth_msg):
            try:
                rgb = self.bridge.imgmsg_to_cv2(rgb_msg, 'rgb8')
                depth_raw = self.bridge.imgmsg_to_cv2(depth_msg, 'passthrough')
                if depth_msg.encoding == '16UC1':
                    depth_m = depth_raw.astype(np.float32) / 1000.0
                elif depth_msg.encoding == '32FC1':
                    depth_m = depth_raw.astype(np.float32)
                else:
                    raise ValueError('depth encoding must be 16UC1 (mm) or 32FC1 (m)')
                if rgb.shape[:2] != depth_m.shape:
                    raise ValueError('depth must be aligned to RGB')
                depth = np.clip(np.nan_to_num(depth_m, nan=0.0, posinf=0.0, neginf=0.0), 0, 6.5535)
                depth = (depth * 10000.0).astype(np.uint16)
                rgb_file, depth_file = io.BytesIO(), io.BytesIO()
                PILImage.fromarray(rgb).save(rgb_file, format='JPEG')
                PILImage.fromarray(depth).save(depth_file, format='PNG')
                stamp = rgb_msg.header.stamp.sec + rgb_msg.header.stamp.nanosec / 1e9
                with self.lock:
                    self.image_seq += 1
                    self.observation = (rgb_file.getvalue(), depth_file.getvalue(), stamp, time.monotonic())
            except Exception as exc:
                self.clear()
                self.get_logger().error('RGB-D input rejected: ' + str(exc))

        def odom_callback(self, msg):
            q = msg.pose.pose.orientation
            yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
            odom = np.array([msg.pose.pose.position.x, msg.pose.pose.position.y, yaw])
            if not np.isfinite(odom).all():
                self.clear()
                return
            stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
            with self.lock:
                self.odom = odom
                self.odom_time = time.monotonic()
                self.odom_queue.append((stamp, odom))

        @staticmethod
        def pose_matrix(odom):
            x, y, yaw = odom
            return np.array([[np.cos(yaw), -np.sin(yaw), 0, x],
                             [np.sin(yaw), np.cos(yaw), 0, y],
                             [0, 0, 1, 0], [0, 0, 0, 1]])

        def control(self):
            try:
                with self.lock:
                    now = time.monotonic()
                    if (self.completed or not self.lease.active() or self.odom is None
                            or now - self.odom_time > args.sensor_timeout or self.observation is None
                            or now - self.observation[3] > args.sensor_timeout):
                        self.clear()
                        return
                    if self.kind == 'trajectory':
                        commands, _ = self.controller.solve(self.odom)
                        v, w = commands[0]
                    elif self.kind == 'actions':
                        v, w, _, _ = self.pid.solve(self.pose_matrix(self.odom), self.goal)
                    else:
                        self.clear()
                        return
                    if self.lease.active():
                        self.publish(v, w)
                    else:
                        self.clear()
            except Exception as exc:
                self.clear()
                self.get_logger().error('Controller stopped: ' + str(exc))

        def apply_prediction(self, response, odom):
            kind, payload = parse_prediction(response)
            if kind == 'stop':
                self.completed = True
                self.clear()
                self.get_logger().info('Model returned STOP. Restart client for a new task.')
                return
            if kind == 'trajectory':
                points = np.asarray(payload)[3:]
                pose = self.pose_matrix(odom)
                homogeneous = np.column_stack((points, np.zeros(len(points)), np.ones(len(points))))
                world = (pose @ homogeneous.T).T[:, :2]
                self.controller = Mpc_controller(world, desired_v=min(0.3, args.max_v),
                                                 v_max=args.max_v, w_max=args.max_w)
                self.goal = None
            else:
                goal = self.pose_matrix(self.odom)
                for action in payload:
                    yaw = math.atan2(goal[1, 0], goal[0, 0])
                    if action == 1:
                        goal[0, 3] += 0.25 * math.cos(yaw)
                        goal[1, 3] += 0.25 * math.sin(yaw)
                    else:
                        angle = math.radians(15 if action == 2 else -15)
                        rotation = np.array([[math.cos(angle), -math.sin(angle), 0],
                                             [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
                        goal[:3, :3] = goal[:3, :3] @ rotation
                self.goal, self.controller = goal, None
            self.kind = kind
            self.lease.renew()

        def plan(self):
            reset, last_seq = True, -1
            while not self.stop_event.wait(0.1):
                with self.lock:
                    if self.completed:
                        continue
                    now = time.monotonic()
                    if (self.observation is None or self.intrinsic is None or self.odom is None
                            or not self.odom_queue or self.image_seq == last_seq
                            or now - self.observation[3] > args.sensor_timeout
                            or now - self.odom_time > args.sensor_timeout):
                        continue
                    last_seq = self.image_seq
                    rgb, depth, stamp, captured = self.observation
                    intrinsic = self.intrinsic
                    odom_stamp, odom = min(self.odom_queue, key=lambda item: abs(item[0] - stamp))
                    if abs(odom_stamp - stamp) > args.sensor_timeout:
                        self.clear()
                        continue
                    odom = odom.copy()
                try:
                    response = requests.post(args.server_url,
                        files={'image': ('rgb.jpg', rgb, 'image/jpeg'), 'depth': ('depth.png', depth, 'image/png')},
                        data={'json': json.dumps({'reset': reset, 'instruction': args.instruction,
                                                  'camera_intrinsic': intrinsic})},
                        timeout=(3.0, args.request_timeout))
                    response.raise_for_status()
                    prediction = response.json()
                    with self.lock:
                        if self.stop_event.is_set():
                            break
                        if time.monotonic() - captured > args.max_observation_age:
                            raise ValueError('prediction is from an expired RGB-D frame')
                        if time.monotonic() - self.odom_time > args.sensor_timeout:
                            raise ValueError('odometry expired during prediction')
                        self.apply_prediction(prediction, odom)
                    reset = False
                except Exception as exc:
                    reset = True
                    self.clear()
                    self.get_logger().error('Prediction failed; stopped: ' + str(exc))
                    self.stop_event.wait(1.0)

    rclpy.init(args=ros_args)
    node = None
    try:
        node = Go2Client()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.stop_event.set()
            node.clear()
            node.worker.join(timeout=0.5)
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
