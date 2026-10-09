import asyncio
import time

import pytest
from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider, validate_architecture
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, "test-start-token")
    with TestClient(app) as value:
        response = value.post("/api/v1/session/exchange", json={"nonce": "test-start-token"})
        value.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
        yield value


def test_auth_origin_csrf_and_nonce(tmp_path):
    with TestClient(create_app(tmp_path, "nonce")) as client:
        assert client.get("/api/v1/projects").status_code == 401
        assert client.post("/api/v1/session/exchange", content='{"nonce":"nonce"}').status_code == 415
        assert (
            client.post(
                "/api/v1/session/exchange",
                json={"nonce": "nonce"},
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
        assert client.post("/api/v1/session/exchange", json={"nonce": "nonce"}).status_code == 200
        assert client.post("/api/v1/session/exchange", json={"nonce": "nonce"}).status_code == 401
        assert client.put("/api/v1/settings/ai", json={"model": "fake"}).status_code == 403


def test_source_opaque_ids_learning_revision_and_consent(client):
    project = client.get("/api/v1/projects").json()["data"][0]
    url = f"/api/v1/projects/{project['id']}"
    detail = client.get(url).json()["data"]
    file_id = detail["file_ids"]["engine.py"]
    assert client.get(f"{url}/files/{file_id}?version_id=wrong").status_code == 409
    assert client.get(f"{url}/files/not-a-file?version_id={project['version_id']}").status_code == 404
    assert (
        "def analyze_project"
        in client.get(f"{url}/files/{file_id}?version_id={project['version_id']}").json()["data"]["text"]
    )
    entity = next(item for item in detail["analysis"]["entities"] if item["visible"])
    payload = {"version_id": project["version_id"], "state": "mastered", "expected_revision": 0}
    assert client.put(f"{url}/learning/{entity['id']}", json=payload).status_code == 200
    assert client.put(f"{url}/learning/{entity['id']}", json=payload).status_code == 409
    assert client.get(url).json()["data"]["progress"]["mastered"] == 1
    assert client.post(url + "/analyze", json={}).status_code == 403
    assert client.get("/api/v1/unknown").status_code == 404


def test_endpoint_change_invalidates_consent(client):
    project = client.get("/api/v1/projects").json()["data"][0]
    url = f"/api/v1/projects/{project['id']}"
    assert client.post(url + "/consent", json={"accepted": True}).status_code == 200
    assert (
        client.put(
            "/api/v1/settings/ai", json={"base_url": "https://example.org/v1", "model": "test"}
        ).status_code
        == 200
    )
    assert client.post(url + "/analyze", json={}).status_code == 403


def test_local_import_worker_handles_chinese_json_and_does_not_execute(client, tmp_path):
    source = tmp_path / "源码样例"
    source.mkdir()
    contents = "raise RuntimeError('禁止执行')\ndef 中文函数():\n    print('仅解析')\n"
    (source / "main.py").write_bytes(contents.encode())
    response = client.post("/api/v1/imports", json={"path": str(source)})
    assert response.status_code == 200
    project_id = response.json()["data"]["id"]
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        project = client.get(f"/api/v1/projects/{project_id}").json()["data"]
        if project["status"] not in {"queued", "indexing"}:
            break
        time.sleep(0.05)
    assert project["status"] == "indexed", project["error"]
    assert project["function_count"] == 1
    assert project["architecture"] is None
    assert (source / "main.py").read_bytes() == contents.encode()


def test_connection_test_contains_no_source(tmp_path, monkeypatch):
    recorded = []

    async def fake(self, system, user, max_tokens):
        recorded.append((system, user, max_tokens))
        return {"connected": True}, {"total_tokens": 4}

    monkeypatch.setattr(Provider, "request", fake)
    response = asyncio.run(Provider(tmp_path).test())
    assert response["source_sent"] is False
    assert "files" not in recorded[0][1] and recorded[0][2] == 256


def test_architecture_rejects_duplicate_cycle_and_wrong_evidence():
    analysis = python_analysis({"x.py": "def f(): pass\n"}, "v")
    entity = analysis.entities[0]
    overview = Overview(introduction="项目", capabilities=["能力"], principle="原理", limitations=[])
    module = Module(id="m", name="模块", summary="职责", code_paths=["x.py"], entity_ids=[entity.id])
    architecture = Architecture(modules=[module], overview=overview)
    validate_architecture(architecture, analysis, {"x.py", "other.py"})
    module.entity_ids.append(entity.id)
    with pytest.raises(ValueError, match="归属"):
        validate_architecture(architecture, analysis, {"x.py"})
    module.entity_ids = [entity.id]
    module.parent_id = "m"
    with pytest.raises(ValueError):
        validate_architecture(architecture, analysis, {"x.py"})
    module.parent_id = None
    module.code_paths = ["other.py"]
    with pytest.raises(ValueError, match="覆盖"):
        validate_architecture(architecture, analysis, {"x.py", "other.py"})
