"""Prediction validation and finite command lifetimes, independent of ROS/SDK."""

import math
import time


def parse_prediction(response):
    """Return (kind, payload); stop or unsupported actions never reuse a plan."""
    if not isinstance(response, dict):
        raise ValueError('prediction must be a JSON object')
    if 'discrete_action' in response and 'trajectory' in response:
        raise ValueError('ambiguous prediction')
    if 'discrete_action' in response:
        actions = response['discrete_action']
        if not isinstance(actions, list) or not actions:
            raise ValueError('empty action sequence')
        if any(type(action) is not int for action in actions):
            raise ValueError('actions must be integers')
        if 0 in actions:
            return 'stop', None
        if any(action not in (1, 2, 3) for action in actions):
            raise ValueError('unsupported action (look-down cannot move this camera)')
        return 'actions', actions
    if 'trajectory' in response:
        points = response['trajectory']
        if not isinstance(points, list) or len(points) < 5:
            raise ValueError('trajectory needs at least five points')
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('trajectory points must be [x, y]')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                raise ValueError('non-finite trajectory')
        return 'trajectory', points
    raise ValueError('prediction has no action or trajectory')


class CommandLease:
    """A new prediction permits commands only for a bounded period."""

    def __init__(self, timeout=2.0, clock=time.monotonic):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        self.timeout, self.clock, self.deadline = timeout, clock, 0.0

    def renew(self):
        self.deadline = self.clock() + self.timeout

    def clear(self):
        self.deadline = 0.0

    def active(self):
        return self.clock() < self.deadline


def clip_command(v, w, max_v, max_w):
    if not all(math.isfinite(value) for value in (v, w, max_v, max_w)):
        return 0.0, 0.0
    return max(0.0, min(max_v, v)), max(-max_w, min(max_w, w))
