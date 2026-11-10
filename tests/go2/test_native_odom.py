"""Validate actual SDK telemetry units/timestamps, without importing ROS."""
import json
import math

import pytest

from onboard.native_odom import parse_native_state, quaternion_from_rpy


def packet(**changes):
    data = dict(receive_monotonic_ns=1_000_000_000, mode=1,
                position=[1.2, -0.4, 0.3], rpy=[0, 0, math.pi / 2],
                velocity=[0.1, 0, 0], yaw_speed=0.2)
    data.update(changes)
    return json.dumps(data).encode()


def test_sdk_receive_age_and_measured_pose_are_preserved():
    state, age = parse_native_state(packet(), now_ns=1_100_000_000)
    assert age == 100_000_000
    assert state['position'] == [1.2, -0.4, 0.3]
    assert state['velocity'] == [0.1, 0, 0]
    q = quaternion_from_rpy(state['rpy'])
    assert q == pytest.approx([0, 0, math.sqrt(0.5), math.sqrt(0.5)])
    assert sum(value * value for value in q) == pytest.approx(1)


@pytest.mark.parametrize('changes', [
    {'receive_monotonic_ns': 1}, {'receive_monotonic_ns': 1_100_000_001},
    {'receive_monotonic_ns': True}, {'position': [1, 2]},
    {'position': [1, float('nan'), 3]}, {'rpy': [0, True, 0]},
    {'velocity': [float('inf'), 0, 0]}, {'yaw_speed': False}, {'mode': True},
])
def test_invalid_or_stale_telemetry_is_not_usable_odometry(changes):
    with pytest.raises(ValueError):
        parse_native_state(packet(**changes), now_ns=1_100_000_000)


def test_oversized_or_nonobject_packets_are_rejected():
    for data in (b'x' * 4097, b'[]', b'not-json'):
        with pytest.raises(ValueError):
            parse_native_state(data, now_ns=1_100_000_000)
