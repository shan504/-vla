"""Validate local SDK pose telemetry before publishing short-range odometry."""

import json
import math
import time


def parse_native_state(data, now_ns=None, max_age=0.5):
    if len(data) > 4096:
        raise ValueError('SDK state packet exceeds 4096 bytes')
    state = json.loads(data)
    if not isinstance(state, dict):
        raise ValueError('SDK state must be an object')
    stamp = state.get('receive_monotonic_ns')
    if type(stamp) is not int or stamp <= 0:
        raise ValueError('SDK receive timestamp is missing or invalid')
    now_ns = time.monotonic_ns() if now_ns is None else now_ns
    age_ns = now_ns - stamp
    if not math.isfinite(max_age) or max_age <= 0 or not 0 <= age_ns <= max_age * 1e9:
        raise ValueError('SDK state is stale or from a future clock')
    for name in ('position', 'rpy', 'velocity'):
        values = state.get(name)
        if not isinstance(values, list) or len(values) != 3 or any(
            type(value) not in (int, float) or not math.isfinite(value) for value in values
        ):
            raise ValueError('SDK state has invalid ' + name)
    yaw_speed = state.get('yaw_speed')
    if type(yaw_speed) not in (int, float) or not math.isfinite(yaw_speed):
        raise ValueError('SDK state has invalid yaw_speed')
    if type(state.get('mode')) is not int:
        raise ValueError('SDK state has invalid mode')
    return state, age_ns


def quaternion_from_rpy(rpy):
    roll, pitch, yaw = (float(value) for value in rpy)
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy)
