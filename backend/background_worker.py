"""Small queue-based worker used by the desktop UI without touching widgets."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any, Callable


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    operation: str
    payload: Any


class BackgroundWorker(threading.Thread):
    """Run one callable and publish success or failure to a thread-safe queue."""

    def __init__(self, events: queue.Queue, operation: str, target: Callable, *args, **kwargs):
        super().__init__(name=f"InvoiceFlow-{operation}", daemon=True)
        self.events = events
        self.operation = operation
        self.target = target
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            result = self.target(*self.args, **self.kwargs)
        except Exception as error:
            self.events.put(WorkerEvent("error", self.operation, error))
        else:
            self.events.put(WorkerEvent("success", self.operation, result))
