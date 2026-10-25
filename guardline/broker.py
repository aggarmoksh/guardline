"""In-process pub/sub for server-sent events. One queue per subscriber; channel names keep public and protected apart."""
import json
import queue
import threading
from typing import Iterator


class Broker:
    def __init__(self) -> None:
        self._subs: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()

    def publish(self, channel: str, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.get(channel, []))
        for q in subs:
            q.put(event)

    def subscribe(self, channel: str, keepalive: float = 15.0) -> Iterator[str]:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(channel, []).append(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = q.get(timeout=keepalive)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with self._lock:
                self._subs[channel].remove(q)
