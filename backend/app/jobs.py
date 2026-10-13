"""
jobs.py

Where background work gets queued. A camera going offline needs a
fresh security audit, but audits are slow and Ring wants a reply
within 5 seconds - so the webhook just drops a job here and returns.

NOTE: this local version is a plain list in memory. On AWS it becomes
an SQS queue; only this file changes, because everything else just
calls enqueue().
"""

import threading


class InMemoryJobQueue:
    def __init__(self):
        self.jobs: list[dict] = []
        self._lock = threading.Lock()   # the server handles requests in parallel

    def enqueue(self, job: dict) -> None:
        with self._lock:
            self.jobs.append(job)

    def clear(self) -> None:
        # NOTE: used by tests to start each test with an empty queue.
        with self._lock:
            self.jobs.clear()


# NOTE: ONE shared queue for the whole app. Other files import this
# object, so a job added by the webhook is the same list a worker reads.
audit_queue = InMemoryJobQueue()