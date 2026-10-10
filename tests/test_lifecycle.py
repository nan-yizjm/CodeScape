import json
import uuid

from codescape.app import create_app
from codescape.instance import LocalInstance
from codescape.lifecycle import ClientLifetime
from fastapi.testclient import TestClient


class Clock:
    def __init__(self):
        self.monotonic = 0
        self.wall = 100000

    def tick(self, lifetime, seconds):
        for _ in range(seconds // 5):
            self.monotonic += 5
            self.wall += 5
            lifetime.should_exit()

    def lifetime(self):
        return ClientLifetime(monotonic=lambda: self.monotonic, wall=lambda: self.wall)


def test_last_page_grace_and_multiple_pages():
    clock = Clock()
    lifetime = clock.lifetime()
    lifetime.heartbeat("a")
    lifetime.heartbeat("b")
    lifetime.disconnect("a")
    clock.tick(lifetime, 100)
    assert not lifetime.should_exit()
    lifetime.heartbeat("b")
    clock.tick(lifetime, 100)
    assert not lifetime.should_exit()
    lifetime.disconnect("b")
    clock.tick(lifetime, 175)
    assert not lifetime.should_exit()
    clock.tick(lifetime, 5)
    assert lifetime.should_exit()


def test_sse_connection_sleep_jump_and_reconnect():
    clock = Clock()
    lifetime = clock.lifetime()
    lifetime.connect("page", "stream")
    clock.tick(lifetime, 400)
    assert not lifetime.should_exit()
    lifetime.close_connection("stream")
    clock.tick(lifetime, 175)
    assert not lifetime.should_exit()
    # A sleeping machine may advance both clocks or only the wall clock.
    clock.wall += 3600
    assert not lifetime.should_exit()
    clock.tick(lifetime, 100)
    lifetime.heartbeat("page")
    assert not lifetime.should_exit()
    clock.monotonic += 3600
    clock.wall += 3600
    assert not lifetime.should_exit()
    clock.tick(lifetime, 175)
    assert not lifetime.should_exit()
    clock.tick(lifetime, 5)
    assert lifetime.should_exit()


def test_client_api_authentication_and_new_single_use_activation(tmp_path):
    app = create_app(tmp_path, "initial")
    with TestClient(app) as client:
        assert client.post("/api/v1/clients/heartbeat", json={"client_id": str(uuid.uuid4())}).status_code == 401
        response = client.post("/api/v1/session/exchange", json={"nonce": "initial"})
        client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
        identity = str(uuid.uuid4())
        assert client.post("/api/v1/clients/heartbeat", json={"client_id": identity}).status_code == 200
        assert identity in app.state.lifetime.clients
        assert client.post("/api/v1/clients/heartbeat", json={"client_id": "../bad"}).status_code == 422
        assert client.post("/api/v1/clients/disconnect", json={"client_id": identity}).status_code == 200
        assert identity not in app.state.lifetime.clients
        activated = app.state.issue_nonce()
        assert client.post("/api/v1/session/exchange", json={"nonce": activated}).status_code == 200
        assert client.post("/api/v1/session/exchange", json={"nonce": activated}).status_code == 401


def test_windows_mutex_and_authenticated_pipe_activation(tmp_path):
    first = LocalInstance(tmp_path)
    second = None
    try:
        assert first.primary
        first.serve(lambda: "http://127.0.0.1:8125/#nonce=test-only")
        second = LocalInstance(tmp_path)
        assert not second.primary
        assert second.activate() == "http://127.0.0.1:8125/#nonce=test-only"
        assert first.authkey not in first.key_path.read_bytes()
        assert json.dumps("test-only").encode() not in first.key_path.read_bytes()
    finally:
        if second:
            second.close()
        first.close()
    reopened = LocalInstance(tmp_path)
    assert reopened.primary
    reopened.close()


def test_authenticated_update_request_does_not_replace_activation(tmp_path):
    requested = []
    first = LocalInstance(tmp_path)
    second = None
    try:
        first.serve(lambda: "http://127.0.0.1:8125/#nonce=owned", lambda: requested.append(True) or True)
        second = LocalInstance(tmp_path)
        assert second._command("shutdown") == {"accepted": True}
        assert requested == [True]
        assert second.activate() == "http://127.0.0.1:8125/#nonce=owned"
    finally:
        if second:
            second.close()
        first.close()
