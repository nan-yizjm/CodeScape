import asyncio
import json
import subprocess
import time
import uuid

import pytest
from codescape.app import create_app
from codescape.database import Database
from codescape.deletion import remove_owned_project
from codescape.provider import Provider
from fastapi.testclient import TestClient


def login(client):
    client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": "test"}).json()["data"]["csrf_token"]


def wait(client, operation, status="completed"):
    for _ in range(200):
        value = client.get(f"/api/v1/operations/{operation['id']}").json()["data"]
        if value["status"] == status:
            return value
        time.sleep(.02)
    raise AssertionError(value)


def import_project(client, source):
    project = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
    for _ in range(200):
        detail = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        if detail["status"] == "indexed":
            return detail
        time.sleep(.02)
    raise AssertionError(detail)


def test_delete_duplicate_isolated_and_confirmation_idempotent(tmp_path):
    source, root = tmp_path / "original", tmp_path / "data"
    source.mkdir()
    original = "raise RuntimeError('禁止执行')\ndef f(): return 1\n"
    (source / "a.py").write_text(original, encoding="utf-8")
    with TestClient(create_app(root, "test", architecture_runner=lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs))) as client:
        login(client)
        one, two = import_project(client, source), import_project(client, source)
        entity = two["analysis"]["entities"][0]
        client.put(f"/api/v1/projects/{two['id']}/learning/{entity['id']}", json={
            "version_id":two["version_id"],"state":"mastered","expected_revision":0})
        for item in [one, two]:
            client.post(f"/api/v1/projects/{item['id']}/consent", json={"accepted":True})
        url = f"/api/v1/projects/{one['id']}"
        assert client.delete(url, params={"confirm": True}).status_code == 415
        assert client.request("DELETE", url, json={"confirm":False}).status_code == 422
        operation = client.request("DELETE", url, json={"confirm":True}).json()["data"]
        assert wait(client, operation)["status"] == "completed"
        assert client.request("DELETE", url, json={"confirm":True}).json()["data"]["id"] == operation["id"]
        assert client.get(url).status_code == 404
        remaining = client.get(f"/api/v1/projects/{two['id']}").json()["data"]
        assert remaining["learning"][entity["id"]]["state"] == "mastered"
        assert not (root / "projects" / one["id"]).exists()
        assert (root / "projects" / two["id"]).exists()
        assert (source / "a.py").read_text(encoding="utf-8") == original
        consents = json.loads((root / "consents.json").read_text(encoding="utf-8"))
        assert not any(key.startswith(one["id"]) for key in consents)
        assert any(key.startswith(two["id"]) for key in consents)
        database = Database(root)
        assert not any(job["project_id"] == one["id"] for job in database.list_jobs())
        assert not database.session(one["id"]) and not database.states(one["id"])
        database.engine.dispose()


def test_delete_waits_for_existing_read_and_rejects_new_read(tmp_path):
    app = create_app(tmp_path, "test")
    with TestClient(app) as client:
        login(client)
        project = client.get("/api/v1/projects").json()["data"][0]
        detail = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        path, file_id = next(iter(detail["file_ids"].items()))
        with app.state.reads.read(project["id"], project["version_id"]):
            operation = client.request("DELETE", f"/api/v1/projects/{project['id']}", json={"confirm":True}).json()["data"]
            assert client.get(f"/api/v1/projects/{project['id']}/files/{file_id}?version_id={project['version_id']}").status_code == 410
            assert (tmp_path / "projects" / project["id"]).exists()
            assert client.post(f"/api/v1/projects/{project['id']}/analyze", json={}).status_code == 410
        wait(client, operation)
    # 删除预置样例后重启不能重新创建它。
    with TestClient(create_app(tmp_path, "test")) as client:
        login(client)
        assert client.get("/api/v1/projects").json()["data"] == []


def test_failed_delete_retry_keeps_records_until_files_removed(tmp_path, monkeypatch):
    from codescape import deletion
    original_remove = deletion.remove_owned_project
    def occupied(*args):
        raise PermissionError("固定文件占用")
    with TestClient(create_app(tmp_path, "test")) as client:
        login(client)
        project = client.get("/api/v1/projects").json()["data"][0]
        monkeypatch.setattr(deletion, "remove_owned_project", occupied)
        url = f"/api/v1/projects/{project['id']}"
        operation = client.request("DELETE", url, json={"confirm":True}).json()["data"]
        wait(client, operation, "failed")
        retained = client.get("/api/v1/projects").json()["data"][0]
        assert retained["status"] == "delete_failed" and retained["delete_operation"]["status"] == "failed"
        assert (tmp_path / "projects" / project["id"]).exists()
        monkeypatch.setattr(deletion, "remove_owned_project", original_remove)
        retry = client.request("DELETE", url, json={"confirm":True}).json()["data"]
        assert retry["id"] == operation["id"]
        wait(client, retry)


def test_restart_finishes_interrupted_delete_without_model_request(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path, "test")) as client:
        login(client)
        project = client.get("/api/v1/projects").json()["data"][0]
    database = Database(tmp_path)
    operation = database.begin_delete(project["id"])
    database.engine.dispose()
    async def forbidden(*args, **kwargs):
        raise AssertionError("删除恢复不得调用模型")
    monkeypatch.setattr(Provider, "request", forbidden)
    with TestClient(create_app(tmp_path, "test")) as client:
        login(client)
        wait(client, operation)
        assert client.get("/api/v1/projects").json()["data"] == []


def test_project_cleanup_removes_junction_only_and_rejects_invalid_id(tmp_path):
    project_id = str(uuid.uuid4())
    owned = tmp_path / "data" / "projects" / project_id
    original = tmp_path / "original"
    owned.mkdir(parents=True)
    original.mkdir()
    (original / "source.py").write_text("raise RuntimeError('不得执行或删除')", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(owned / "linked"), str(original)], capture_output=True, check=True)
    remove_owned_project(tmp_path / "data", project_id)
    assert (original / "source.py").exists() and not owned.exists()
    with pytest.raises(ValueError):
        remove_owned_project(tmp_path / "data", "../original")


def test_delete_cancels_running_model_task_and_preserves_another_project(tmp_path, monkeypatch):
    import threading
    entered, cancelled = threading.Event(), threading.Event()
    async def fixed(*args, **kwargs):
        entered.set()
        try:
            await asyncio.sleep(1000)
        finally:
            cancelled.set()
    monkeypatch.setattr(Provider, "architecture", fixed)
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.py").write_text("def f(): return 1", encoding="utf-8")
    root = tmp_path / "data"
    with TestClient(create_app(root, "test", architecture_runner=lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs))) as client:
        login(client)
        project = import_project(client, source)
        url = f"/api/v1/projects/{project['id']}"
        client.post(url + "/consent", json={"accepted":True})
        client.post(url + "/analyze", json={})
        assert entered.wait(3)
        operation = client.request("DELETE",url,json={"confirm":True}).json()["data"]
        wait(client, operation)
        assert cancelled.is_set()
        assert (source / "a.py").exists()
        assert len(client.get("/api/v1/projects").json()["data"]) == 1


def test_real_windows_file_lock_keeps_delete_retryable(tmp_path):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                                  wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    with TestClient(create_app(tmp_path, "test")) as client:
        login(client)
        project = client.get("/api/v1/projects").json()["data"][0]
        url = f"/api/v1/projects/{project['id']}"
        detail = client.get(url).json()["data"]
        file = tmp_path / "projects" / project["id"] / "snapshots" / detail["scope"]["snapshot_id"] / next(iter(detail["file_ids"]))
        assert file.resolve().is_relative_to((tmp_path / "projects" / project["id"]).resolve())
        handle = kernel.CreateFileW(str(file),0x80000000,0,None,3,0x80,None)
        assert handle != ctypes.c_void_p(-1).value
        try:
            operation = client.request("DELETE",url,json={"confirm":True}).json()["data"]
            wait(client, operation, "failed")
            assert file.exists()
            assert client.get("/api/v1/projects").json()["data"][0]["status"] == "delete_failed"
        finally:
            kernel.CloseHandle(handle)
        retry = client.request("DELETE",url,json={"confirm":True}).json()["data"]
        wait(client, retry)
        assert not (tmp_path / "projects" / project["id"]).exists()
