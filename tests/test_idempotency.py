import asyncio
import hashlib
import json
import time
import uuid

import httpx
import pytest
from codescape.app import create_app
from codescape.database import Database
from codescape.idempotency import PREFIX, RETENTION_SECONDS, MutationReceipts, protected_request
from codescape.imports import ImportService
from fastapi.testclient import TestClient
from sqlalchemy import select


def authenticated(client, nonce):
    response = client.post("/api/v1/session/exchange", json={"nonce": nonce})
    client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]


def owned_source(root):
    source = root / "owned-source"
    source.mkdir()
    (source / "owned.py").write_text("raise RuntimeError('must not execute')\ndef owned():return 1\n", encoding="utf-8")
    return source


def test_import_retry_and_restart_return_same_project_with_encrypted_receipt(tmp_path):
    source = owned_source(tmp_path)
    key = str(uuid.uuid4())
    app = create_app(tmp_path / "data", "first")
    with TestClient(app) as client:
        authenticated(client, "first")
        response = client.post("/api/v1/imports", json={"path": str(source)}, headers={"Idempotency-Key": key})
        assert response.status_code == 200
        again = client.post("/api/v1/imports", json={"path": str(source)}, headers={"Idempotency-Key": key})
        assert again.status_code == 200 and again.json() == response.json()
        assert again.headers["Idempotency-Replayed"] == "true"
        assert len(client.get("/api/v1/projects").json()["data"]) == 2
    restarted = create_app(tmp_path / "data", "second")
    with TestClient(restarted) as client:
        authenticated(client, "second")
        restored = client.post("/api/v1/imports", json={"path": str(source)}, headers={"Idempotency-Key": key})
        assert restored.json() == response.json()
        assert len(client.get("/api/v1/projects").json()["data"]) == 2
    database = Database(tmp_path / "data")
    try:
        with database.engine.connect() as connection:
            rows = list(connection.execute(select(database.app_metadata).where(database.app_metadata.c.key.startswith(PREFIX))).mappings())
        assert len(rows) == 1 and rows[0]["value"]["status"] == "complete"
        encoded = json.dumps(dict(rows[0]))
        assert key not in encoded and str(source) not in encoded and "must not execute" not in encoded
        assert rows[0]["value"]["expires_at"] - rows[0]["value"]["created_at"] >= RETENTION_SECONDS
    finally:
        database.engine.dispose()


def test_different_body_or_operation_with_same_key_conflicts(tmp_path):
    source = owned_source(tmp_path)
    with TestClient(create_app(tmp_path / "data", "nonce")) as client:
        authenticated(client, "nonce")
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        first = client.post("/api/v1/imports", json={"path": str(source), "includes": []}, headers=headers)
        assert first.status_code == 200
        conflict = client.post("/api/v1/imports", json={"path": str(source), "includes": ["**"]}, headers=headers)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        project_id = first.json()["data"]["id"]
        assert client.request("DELETE", f"/api/v1/projects/{project_id}", json={"confirm": True}, headers=headers).status_code == 409
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 200


def test_authentication_and_csrf_are_checked_before_replay(tmp_path):
    source = owned_source(tmp_path)
    app = create_app(tmp_path / "data", "nonce")
    with TestClient(app) as client:
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).status_code == 401
        authenticated(client, "nonce")
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).status_code == 200
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers={**headers, "X-CSRF-Token": "wrong"}).status_code == 403
        client.cookies.clear()
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).status_code == 401


def test_corrupt_receipt_never_repeats_operation(tmp_path):
    source = owned_source(tmp_path)
    key = str(uuid.uuid4())
    app = create_app(tmp_path / "data", "nonce")
    with TestClient(app) as client:
        authenticated(client, "nonce")
        headers = {"Idempotency-Key": key}
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).status_code == 200
        app.state.receipts.update(PREFIX + hashlib.sha256(key.encode()).hexdigest(), response="invalid-hex")
        response = client.post("/api/v1/imports", json={"path": str(source)}, headers=headers)
        assert response.status_code == 503 and response.json()["error"]["code"] == "REQUEST_RECEIPT"
        assert len(client.get("/api/v1/projects").json()["data"]) == 2


def test_receipt_storage_failure_prevents_domain_action(tmp_path, monkeypatch):
    source = owned_source(tmp_path)
    app = create_app(tmp_path / "data", "nonce")
    with TestClient(app) as client:
        authenticated(client, "nonce")
        monkeypatch.setattr(app.state.receipts, "claim", lambda *args: (_ for _ in ()).throw(OSError("fixed-failure")))
        response = client.post("/api/v1/imports", json={"path": str(source)}, headers={"Idempotency-Key": str(uuid.uuid4())})
        assert response.status_code == 503 and response.json()["error"]["code"] == "REQUEST_STORAGE"
        assert len(client.get("/api/v1/projects").json()["data"]) == 1


def test_response_save_failure_does_not_repeat_committed_import(tmp_path, monkeypatch):
    source = owned_source(tmp_path)
    app = create_app(tmp_path / "data", "nonce")
    with TestClient(app) as client:
        authenticated(client, "nonce")
        original = app.state.receipts.update
        def update(key, **values):
            if values.get("status") == "complete":
                raise OSError("simulate-after-domain-commit")
            return original(key, **values)
        monkeypatch.setattr(app.state.receipts, "update", update)
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        assert client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).status_code == 503
        response = client.post("/api/v1/imports", json={"path": str(source)}, headers=headers)
        assert response.status_code == 409 and response.json()["error"]["code"] == "REQUEST_INDETERMINATE"
        assert len(client.get("/api/v1/projects").json()["data"]) == 2


def test_restart_recovery_retention_and_expiry_do_not_discard_live_request(tmp_path):
    database = Database(tmp_path)
    moment = [100.0]
    receipts = MutationReceipts(database, clock=lambda: moment[0])
    first, value = receipts.claim(PREFIX + "owned", "digest")
    assert first and value["status"] == "processing"
    moment[0] += RETENTION_SECONDS + 1
    assert not receipts.claim(PREFIX + "owned", "digest")[0]
    receipts.recover()
    assert receipts.claim(PREFIX + "owned", "digest")[1]["status"] == "indeterminate"
    moment[0] += RETENTION_SECONDS - 1
    assert not receipts.claim(PREFIX + "owned", "digest")[0]
    moment[0] += 2
    assert receipts.claim(PREFIX + "owned", "new-digest")[0]
    database.engine.dispose()


def test_concurrent_duplicate_imports_only_execute_once(tmp_path, monkeypatch):
    async def scenario():
        source = owned_source(tmp_path)
        app = create_app(tmp_path / "data", "nonce")
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        original = ImportService.preview
        def preview(self, body):
            calls.append(body.path)
            return original(self, body)
        monkeypatch.setattr(ImportService, "preview", preview)
        original_next = app.state.receipts.handle
        async def handle(request, call_next):
            async def delayed(request):
                if request.url.path == "/api/v1/imports":
                    entered.set()
                    await release.wait()
                return await call_next(request)
            return await original_next(request, delayed)
        monkeypatch.setattr(app.state.receipts, "handle", handle)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                bootstrap = await client.post("/api/v1/session/exchange", json={"nonce": "nonce"})
                client.headers["X-CSRF-Token"] = bootstrap.json()["data"]["csrf_token"]
                headers = {"Idempotency-Key": str(uuid.uuid4())}
                first = asyncio.create_task(client.post("/api/v1/imports", json={"path": str(source)}, headers=headers))
                await entered.wait()
                second = asyncio.create_task(client.post("/api/v1/imports", json={"path": str(source)}, headers=headers))
                await asyncio.sleep(0.05)
                release.set()
                a, b = await asyncio.gather(first, second)
                assert a.status_code == b.status_code == 200 and a.json() == b.json()
                assert calls == [str(source)]
                assert b.headers["Idempotency-Replayed"] == "true"
    asyncio.run(scenario())


def test_project_deletion_redacts_receipt_and_does_not_restore_old_import(tmp_path):
    source = owned_source(tmp_path)
    app = create_app(tmp_path / "data", "nonce")
    with TestClient(app) as client:
        authenticated(client, "nonce")
        headers = {"Idempotency-Key": str(uuid.uuid4())}
        created = client.post("/api/v1/imports", json={"path": str(source)}, headers=headers).json()["data"]
        url = f"/api/v1/projects/{created['id']}"
        deletion_headers = {"Idempotency-Key": str(uuid.uuid4())}
        response = client.request("DELETE", url, json={"confirm": True}, headers=deletion_headers)
        assert response.status_code == 202
        operation = response.json()["data"]["id"]
        for _ in range(100):
            if client.get(f"/api/v1/operations/{operation}").json()["data"]["status"] == "completed":
                break
            time.sleep(.01)
        else:
            pytest.fail("删除未完成")
        replay = client.post("/api/v1/imports", json={"path": str(source)}, headers=headers)
        assert replay.status_code == 410 and replay.json()["error"]["code"] == "PROJECT_DELETED"
        assert client.request("DELETE", url, json={"confirm": True}, headers=deletion_headers).status_code == 202
        assert len(client.get("/api/v1/projects").json()["data"]) == 1
        assert (source / "owned.py").is_file()


def test_corrupt_record_on_restart_fails_closed(tmp_path):
    source = owned_source(tmp_path)
    key = str(uuid.uuid4())
    database = Database(tmp_path / "data")
    database.metadata_value(PREFIX + hashlib.sha256(key.encode()).hexdigest(), "invalid-fixed-receipt")
    database.engine.dispose()
    with TestClient(create_app(tmp_path / "data", "nonce")) as client:
        authenticated(client, "nonce")
        response = client.post("/api/v1/imports", json={"path": str(source)}, headers={"Idempotency-Key": key})
        assert response.status_code == 503 and response.json()["error"]["code"] == "REQUEST_RECEIPT"
        assert len(client.get("/api/v1/projects").json()["data"]) == 1


@pytest.mark.parametrize("method,path", [("POST", "/api/v1/imports"), ("POST", "/api/v1/tasks/j/start"),
    ("POST", "/api/v1/tasks/j/pause"), ("POST", "/api/v1/tasks/j/cancel"), ("DELETE", "/api/v1/projects/p"),
    ("POST", "/api/v1/projects/p/reanalysis"), ("POST", "/api/v1/projects/p/analyze"),
    ("PUT", "/api/v1/tasks/order"), ("POST", "/api/v1/settings/ai/test")])
def test_protected_routes(method, path):
    assert protected_request(method, path)


@pytest.mark.parametrize("method,path", [("PUT", "/api/v1/settings/ai"), ("GET", "/api/v1/projects"),
    ("PUT", "/api/v1/projects/p/learning/f"), ("PUT", "/api/v1/projects/p/session"),
    ("POST", "/api/v1/session/exchange"), ("POST", "/api/v1/clients/heartbeat")])
def test_credentials_revisions_sessions_and_heartbeats_are_not_cached(method, path):
    assert not protected_request(method, path)
