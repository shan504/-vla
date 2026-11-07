"""Software-only stop/watchdog tests. These do not run ROS, SDK or model inference."""

import importlib.util
import struct
from pathlib import Path

import pytest

from go2.robot_control import CommandLease, clip_command, parse_prediction


@pytest.mark.parametrize('prediction', [None, {}, {'discrete_action': []}, {'discrete_action': [5]},
    {'discrete_action': [True]}, {'trajectory': []}, {'trajectory': [[float('nan'), 0]] * 5},
    {'trajectory': [[0, 1, 2]] * 5}, {'discrete_action': [1], 'trajectory': [[0, 0]] * 5}])
def test_invalid_predictions_are_rejected(prediction):
    with pytest.raises(ValueError):
        parse_prediction(prediction)


def test_model_stop_is_terminal_even_in_a_sequence():
    assert parse_prediction({'discrete_action': [1, 0, 2]}) == ('stop', None)
    assert parse_prediction({'discrete_action': [1, 2, 3]}) == ('actions', [1, 2, 3])
    assert parse_prediction({'trajectory': [[i / 10, 0] for i in range(8)]})[0] == 'trajectory'


def test_expired_prediction_cannot_keep_commands_alive():
    now = [10.0]
    lease = CommandLease(2.0, lambda: now[0])
    assert not lease.active()
    lease.renew()
    assert lease.active()
    now[0] = 12.0
    assert not lease.active()
    lease.renew()
    lease.clear()
    assert not lease.active()


def test_velocity_is_bounded_and_nan_stops():
    assert clip_command(12, -10, 0.3, 0.4) == (0.3, -0.4)
    assert clip_command(-1, 0.2, 0.3, 0.4) == (0.0, 0.2)
    assert clip_command(float('nan'), 0.2, 0.3, 0.4) == (0.0, 0.0)


def test_driver_watchdog_drops_expired_malformed_and_nan_packets():
    spec = importlib.util.spec_from_file_location('go2_command_guard',
        Path(__file__).resolve().parents[2] / 'onboard' / 'command_guard.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    now = [10.0]
    guard = module.CommandWatchdog(clock=lambda: now[0])
    assert guard.current() == (0.0, 0.0)
    guard.accept(struct.pack('<ff', 1.0, -1.0))
    assert guard.current() == (0.3, -0.4)
    now[0] += 0.5
    assert guard.current() == (0.0, 0.0)
    guard.accept(struct.pack('<ff', 0.2, 0.1))
    assert not guard.accept(b'bad')
    assert guard.current() == (0.0, 0.0)
    assert not guard.accept(struct.pack('<ff', float('nan'), 1.0))
    assert guard.current() == (0.0, 0.0)
