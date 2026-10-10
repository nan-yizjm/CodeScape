import asyncio
import json
import time

import pytest
from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.models import Analysis, Architecture, Entity, Location, Module, Overview, Scope
from codescape.provider import (
    Provider,
    ProviderError,
    normalize_module_paths,
    repair_batch_architecture,
    validate_architecture,
)
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


def test_retry_index_uses_existing_snapshot(client, tmp_path, monkeypatch):
    from codescape.database import Database
    source = tmp_path / "retry-source"
    source.mkdir()
    (source / "x.py").write_text("def original(): pass", encoding="utf-8")
    project = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
    url = f"/api/v1/projects/{project['id']}"
    for _ in range(100):
        detail = client.get(url).json()["data"]
        if detail["status"] == "indexed":
            break
        time.sleep(0.02)
    db = Database(tmp_path)
    db.update(project["id"], result=None, status="failed", error="解析中断")
    db.engine.dispose()
    (source / "x.py").write_text("def changed(): pass", encoding="utf-8")
    assert client.post(url + "/index", json={}).status_code == 200
    for _ in range(100):
        detail = client.get(url).json()["data"]
        if detail["status"] == "indexed":
            break
        time.sleep(0.02)
    assert detail["status"] == "indexed"
    assert [entity["name"] for entity in detail["analysis"]["entities"]] == ["original"]
    assert client.post(url + "/index", json={}).status_code == 409


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


def test_import_preview_requires_confirmation_and_uses_scanned_snapshot(client, tmp_path):
    source = tmp_path / "预览样例"
    source.mkdir()
    original = "def original(): pass\r\n"
    (source / "a.py").write_bytes(original.encode("utf-8"))
    before = len(client.get("/api/v1/projects").json()["data"])
    preview = client.post("/api/v1/imports/preview", json={"path": str(source)}).json()["data"]
    assert preview["file_count"] == 1 and preview["files"] == ["a.py"]
    assert len(client.get("/api/v1/projects").json()["data"]) == before
    (source / "a.py").write_text("raise RuntimeError('不执行新源码')", encoding="utf-8")
    assert client.post("/api/v1/imports", json={"preview_id": preview["preview_id"], "excludes": ["a.py"]}).status_code == 400
    response = client.post("/api/v1/imports", json={"preview_id": preview["preview_id"]})
    assert response.status_code == 200
    project = response.json()["data"]
    assert project["origin"]["kind"] == "local"
    assert client.post("/api/v1/imports", json={"preview_id": preview["preview_id"]}).status_code == 400
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        detail = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        if detail["status"] == "indexed":
            break
        time.sleep(0.05)
    file_id = detail["file_ids"]["a.py"]
    saved = client.get(f"/api/v1/projects/{project['id']}/files/{file_id}?version_id={project['version_id']}").json()["data"]
    assert saved["text"] == original


def test_connection_test_contains_no_source(tmp_path, monkeypatch):
    recorded = []

    async def fake(self, system, user, max_tokens):
        recorded.append((system, user, max_tokens))
        return {"connected": True}, {"total_tokens": 4}

    monkeypatch.setattr(Provider, "request", fake)
    response = asyncio.run(Provider(tmp_path).test())
    assert response["source_sent"] is False
    assert "files" not in recorded[0][1] and recorded[0][2] == 256


def test_refresh_overview_requires_consent_and_preserves_architecture(client, monkeypatch):
    project = client.get("/api/v1/projects").json()["data"][0]
    url = f"/api/v1/projects/{project['id']}"
    before = client.get(url).json()["data"]
    assert client.post(url + "/overview", json={}).status_code == 403
    client.post(url + "/consent", json={"accepted": True})

    async def fake(self, architecture, analysis):
        return Overview(introduction="示例用于学习源码协作。", capabilities=["理解模块协作"],
                        principle="输入经过分析后变成图形。", limitations=["预置验证样例"],
                        synthesized=True), {"total_tokens": 5}

    monkeypatch.setattr(Provider, "project_overview", fake)
    response = client.post(url + "/overview", json={})
    assert response.status_code == 200
    after = response.json()["data"]
    assert after["architecture"]["overview"]["synthesized"]
    for field in ("version_id", "status", "analysis", "learning", "scope", "file_ids"):
        assert after[field] == before[field]
    assert after["architecture"]["modules"] == before["architecture"]["modules"]

    async def failed(self, architecture, analysis):
        raise ProviderError("模拟概览失败")

    monkeypatch.setattr(Provider, "project_overview", failed)
    assert client.post(url + "/overview", json={}).status_code == 503
    assert client.get(url).json()["data"]["architecture"] == after["architecture"]

    # 失败后锁释放，接收端变化需要重新授权，不能沿用旧授权。
    client.put("/api/v1/settings/ai", json={"base_url": "https://example.org", "model": "test"})
    assert client.post(url + "/overview", json={}).status_code == 403


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
        if "confirmed_collaboration" in payload:
            assert len(payload["modules"]) == 2
            return {"introduction": "这是一个示例项目。", "capabilities": ["处理两类输入"],
                    "principle": "两类模块各自处理输入。", "limitations": []}, {"total_tokens": 3}
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
    assert usage["total_tokens"] == 23
    assert architecture.overview.synthesized
    assert architecture.overview.introduction == "这是一个示例项目。"
    assert progress[0][0] == 0 and progress[-1][0] == progress[-1][1] == 2
    assert len(requests) == 3 and len(batches) == 2
    requests.clear()
    resumed, resumed_usage = asyncio.run(
        provider.architecture(analysis, files, completed_batches=batches[:1], on_batch=on_batch)
    )
    assert len(resumed.modules) == 2
    assert len(requests) == 2 and resumed_usage["total_tokens"] == 13


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
    module.code_paths = []
    normalize_module_paths(architecture, analysis)
    assert module.code_paths == ["x.py"]
    validate_architecture(architecture, analysis, {"x.py", "other.py"})
    repaired = Architecture(
        modules=[Module(id="bad", name="错误归属", summary="职责", code_paths=["other.py"], entity_ids=["missing"])],
        overview=overview,
    )
    repair_batch_architecture(repaired, analysis, {entity.id}, {"x.py"})
    validate_architecture(repaired, analysis, {"x.py"}, expected_entity_ids={entity.id})
    assert any(module.incomplete and entity.id in module.entity_ids for module in repaired.modules)
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


def test_architecture_replans_when_model_output_is_truncated(monkeypatch, tmp_path):
    provider = Provider(tmp_path)
    files = {"a.py": "def a(): pass\n", "b.py": "def b(): pass\n"}
    analysis = python_analysis(files, "v")
    calls = 0

    async def fake_request(self, system, user, max_tokens=8000):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ProviderError("模型输出被截断，请缩小分析批次。")
        payload = json.loads(user)
        if "confirmed_collaboration" in payload:
            return {"introduction": "这是一个示例项目。", "capabilities": ["处理输入"],
                    "principle": "两个模块共同处理输入。", "limitations": []}, {"total_tokens": 1}
        response = {
            "modules": [
                {"id": entity["id"], "name": entity["name"], "summary": "职责",
                 "entity_ids": [entity["id"]], "code_paths": [entity["location"]["path"]], "incomplete": False}
                for entity in payload["entities"]
            ],
            "overview": {"introduction": "项目", "capabilities": ["能力"],
                          "principle": "原理", "limitations": []},
        }, {"total_tokens": 1}
        if calls == 3:
            response[0]["type"] = "json_object"
        return response

    monkeypatch.setattr(Provider, "request", fake_request)
    architecture, _ = asyncio.run(provider.architecture(analysis, files))
    assert calls == 4
    assert {entity_id for module in architecture.modules for entity_id in module.entity_ids} == {
        entity.id for entity in analysis.entities
    }


def test_checkpoint_alignment_merges_dynamic_subbatches(tmp_path):
    provider = Provider(tmp_path)
    files = {"a.py": "def a(): pass\ndef aa(): pass\n", "b.py": "def b(): pass\n"}
    analysis = python_analysis(files, "v")
    by_path = {path: [entity for entity in analysis.entities if entity.location.path == path] for path in files}
    overview = Overview(introduction="项目", capabilities=["能力"], principle="原理", limitations=[])

    def result(name, entities, path):
        return Architecture(
            modules=[Module(id=name, name=name, summary="职责", entity_ids=[entity.id for entity in entities], code_paths=[path])],
            overview=overview,
        )

    batches = [
        ({"a.py": files["a.py"]}, {"a.py": (1, 2)}),
        ({"b.py": files["b.py"]}, {"b.py": (1, 1)}),
    ]
    aligned = provider._align_completed_batches(
        [result("b", by_path["b.py"], "b.py"), result("a1", by_path["a.py"][:1], "a.py"),
         result("a2", by_path["a.py"][1:], "a.py")],
        batches,
        analysis,
    )
    assert len(aligned) == 2
    assert {entity_id for module in aligned[0].modules for entity_id in module.entity_ids} == {
        entity.id for entity in by_path["a.py"]
    }
