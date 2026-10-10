from __future__ import annotations

import threading
import time
from collections import Counter
from contextlib import contextmanager


class VersionReads:
    """Short client leases and in-flight reads; never a historical browsing API."""

    def __init__(self, clock=time.monotonic, ttl: float = 60):
        self.clock, self.ttl = clock, ttl
        self.lock = threading.RLock()
        self.leases: dict[tuple[str, str, str], float] = {}
        self.readers: Counter = Counter()
        self.revoked: set[str] = set()

    def _expire(self):
        timestamp = self.clock()
        self.leases = {key: expiry for key, expiry in self.leases.items() if expiry > timestamp}

    def held(self, client: str, project: str, version: str) -> bool:
        with self.lock:
            self._expire()
            return project not in self.revoked and (client, project, version) in self.leases

    def renew(self, client: str, versions: list[tuple[str, str, float]]):
        with self.lock:
            self._expire()
            self.leases = {key: expiry for key, expiry in self.leases.items() if key[0] != client}
            for project, version, remaining in versions:
                if project not in self.revoked and remaining > 0:
                    self.leases[client, project, version] = self.clock() + min(self.ttl, remaining)

    def disconnect(self, client: str):
        self.renew(client, [])

    def revoke(self, project: str):
        with self.lock:
            self.revoked.add(project)
            self.leases = {key: expiry for key, expiry in self.leases.items() if key[1] != project}

    @contextmanager
    def read(self, project: str, version: str):
        key = project, version
        with self.lock:
            if project in self.revoked:
                raise ValueError("项目正在删除，不能继续读取。")
            self.readers[key] += 1
        try:
            yield
        finally:
            with self.lock:
                self.readers[key] -= 1
                if not self.readers[key]:
                    del self.readers[key]

    @contextmanager
    def reclaim(self, project: str, version: str):
        # Hold the boundary lock until deletion/tombstone publication finishes.
        with self.lock:
            self._expire()
            available = not self.readers.get((project, version), 0) and not any(
                key[1:] == (project, version) for key in self.leases)
            yield available
