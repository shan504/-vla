"""Finite, bounded UDP commands; no SDK or ROS dependency."""

import math
import struct
import time


class CommandWatchdog:
    def __init__(self, timeout=0.5, max_v=0.3, max_w=0.4, clock=time.monotonic):
        if not all(math.isfinite(v) and v > 0 for v in (timeout, max_v, max_w)):
            raise ValueError('timeout and velocity limits must be positive and finite')
        self.timeout, self.max_v, self.max_w, self.clock = timeout, max_v, max_w, clock
        self.clear()

    def clear(self):
        self.command = (0.0, 0.0)
        self.last_time = float('-inf')

    def accept(self, data):
        try:
            v, w = struct.unpack('<ff', data)
            if not math.isfinite(v) or not math.isfinite(w):
                raise ValueError('non-finite UDP command')
        except (ValueError, struct.error):
            self.clear()
            return False
        self.command = (max(0.0, min(self.max_v, v)), max(-self.max_w, min(self.max_w, w)))
        self.last_time = self.clock()
        return True

    def current(self):
        if self.clock() - self.last_time >= self.timeout:
            self.clear()
        return self.command
