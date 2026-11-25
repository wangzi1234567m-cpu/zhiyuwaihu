"""Opt-in bounded local timings. Never retain PCM, text, URLs or credentials."""
import time
from collections import deque


def now_ns():
    return time.perf_counter_ns()


class RealtimeProbe:
    def __init__(self, enabled=False, capacity=12000):
        self.enabled = enabled
        self.events = deque(maxlen=capacity)
        self.dropped = 0

    def emit(self, event, **fields):
        if self.enabled:
            if len(self.events) == self.events.maxlen:
                self.dropped += 1
            self.events.append({'event': event, 'at_ns': now_ns(), **fields})

    def snapshot(self):
        return {'enabled': self.enabled, 'clock_domain': 'server.perf_counter',
                'resolution_ns': time.get_clock_info('perf_counter').resolution * 1e9,
                'dropped': self.dropped, 'events': list(self.events)}
