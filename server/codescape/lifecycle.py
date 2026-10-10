from __future__ import annotations

import time


class ClientLifetime:
    def __init__(self, heartbeat_timeout: float = 120, grace: float = 60,
                 monotonic=time.monotonic, wall=time.time):
        self.timeout, self.grace = heartbeat_timeout, grace
        self.monotonic, self.wall = monotonic, wall
        self.clients: dict[str, float] = {}
        self.connections: dict[str, str] = {}
        self.last_activity = monotonic()
        self.previous_monotonic, self.previous_wall = self.last_activity, wall()

    def heartbeat(self, client_id: str) -> None:
        self.observe_clock()
        moment = self.monotonic()
        self.clients[client_id] = moment
        self.last_activity = moment

    def disconnect(self, client_id: str) -> None:
        self.observe_clock()
        if client_id in self.clients:
            self.clients.pop(client_id)
            self.last_activity = self.monotonic()

    def connect(self, client_id: str, connection_id: str) -> None:
        self.connections[connection_id] = client_id
        self.heartbeat(client_id)

    def close_connection(self, connection_id: str) -> None:
        if self.connections.pop(connection_id, None) is not None:
            self.last_activity = self.monotonic()

    def observe_clock(self) -> None:
        moment, wall = self.monotonic(), self.wall()
        elapsed = moment - self.previous_monotonic
        # Windows clocks may both advance through sleep, or only the wall clock
        # may advance. A missed interval starts a full new connection grace.
        if elapsed > 30 or elapsed < 0 or abs((wall - self.previous_wall) - elapsed) > 30:
            self.last_activity = moment
            self.clients = {client: moment for client in self.clients}
        self.previous_monotonic, self.previous_wall = moment, wall

    def should_exit(self) -> bool:
        self.observe_clock()
        moment = self.monotonic()
        self.clients = {client: seen for client, seen in self.clients.items() if moment - seen < self.timeout}
        return not self.connections and not self.clients and moment - self.last_activity >= self.timeout + self.grace
