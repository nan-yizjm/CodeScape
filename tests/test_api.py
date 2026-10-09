import asyncio
import json
import time

import pytest
from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.models import Analysis, Architecture, Entity, Location, Module, Overview, Scope
from codescape.provider import Provider, ProviderError, validate_architecture
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


def test_architecture_splits_large_context_and_merges_checked_batches(tmp_path, monkeypatch):
    provider = Provider(tmp_path)
    files = {"one.py": "# one\n" + "x" * 90_000, "two.py": "# two\n" + "y" * 90_000}
    entities = [
        Entity(
            id="one-f", name="one", qualified_name="one", signature="def one()", language="python",
            location=Location(path="one.py", start_line=1, end_line=1, start_byte=0, end_byte=1),
            scope_id="one-s", implementation_hash="h1",
        ),
        Entity(
            id="two-f", name="two", qualified_name="two", signature="def two()", language="python",
            location=Location(path="two.py", start_line=1, end_line=1, start_byte=0, end_byte=1),
            scope_id="two-s", implementation_hash="h2",
        ),
    ]
    analysis = Analysis(
        entities=entities,
        scopes=[
            Scope(id="one-s", kind="function", name="one", location=entities[0].location),
            Scope(id="two-s", kind="function", name="two", location=entities[1].location),
        ],
    )
    progress = []
    batches = []
    requests = []

    async def fake_request(self, system, user, max_tokens=8000):
        requests.append(user)
        payload = json.loads(user)
        modules = [
            {"id": f"local-{entity['id']}", "name": entity["name"], "summary": "批次职责",
             "entity_ids": [entity["id"]], "code_paths": [entity["location"]["path"]], "incomplete": False}
            for entity in payload["entities"]
        ]
        return {
            "modules": modules,
            "overview": {"introduction": "批次项目", "capabilities": ["批次能力"],
                          "principle": "批次原理", "limitations": []},
        }, {"total_tokens": 10}

    monkeypatch.setattr(Provider, "request", fake_request)

    async def on_progress(completed, total, detail):
        progress.append((completed, total, detail))

    async def on_batch(completed, total, architecture):
        batches.append(architecture)

    architecture, usage = asyncio.run(
        provider.architecture(analysis, files, on_progress=on_progress, on_batch=on_batch)
    )
    assert len(architecture.modules) == 2
    assert {entity_id for module in architecture.modules for entity_id in module.entity_ids} == {"one-f", "two-f"}
    assert usage["total_tokens"] == 20
    assert progress[0][0] == 0 and progress[-1][0] == progress[-1][1] == 2
    assert len(requests) == len(batches) == 2
    requests.clear()
    resumed, resumed_usage = asyncio.run(
        provider.architecture(analysis, files, completed_batches=batches[:1], on_batch=on_batch)
    )
    assert len(resumed.modules) == 2
    assert len(requests) == 1 and resumed_usage["total_tokens"] == 10


def test_ai_analysis_resumes_from_last_checkpoint(client, monkeypatch):
    project = client.get("/api/v1/projects").json()["data"][0]
    url = f"/api/v1/projects/{project['id']}"
    assert client.post(url + "/consent", json={"accepted": True}).status_code == 200
    architecture = Architecture.model_validate(client.get(url).json()["data"]["architecture"])
    calls = []

    async def fake_architecture(self, analysis, files, on_progress=None, on_batch=None, completed_batches=None):
        resumed_count = len(completed_batches or [])
        calls.append(resumed_count)
        if not resumed_count:
            await on_batch(1, 2, architecture)
            raise ProviderError("模拟第 1 批中断")
        await on_batch(2, 2, architecture)
        return architecture, {"total_tokens": 2}

    monkeypatch.setattr(Provider, "architecture", fake_architecture)
    assert client.post(url + "/analyze", json={}).status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = client.get(url).json()["data"]
        if current["status"] == "paused":
            break
        time.sleep(0.05)
    assert current["status"] == "paused"
    checkpoint = client.app.state.root / "projects" / project["id"] / "architecture-checkpoint.json"
    assert checkpoint.exists()
    assert client.post(url + "/analyze", json={}).status_code == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = client.get(url).json()["data"]
        if current["status"] == "ready":
            break
        time.sleep(0.05)
    assert current["status"] == "ready"
    assert current["analysis_progress"]["phase"] == "completed"
    assert calls == [0, 1]
    assert not checkpoint.exists()


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
