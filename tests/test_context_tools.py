import asyncio
import json

import httpx
import pytest
from codescape.analyzers import python_analysis
from codescape.context_tools import ReadRequest, SnapshotTools, validate_explanations
from codescape.database import Database
from codescape.ledger import RequestLedger
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.provider import Provider, ProviderError


def fixture():
    files = {"a.py": "def first():\n    return 1\n", "b.py": "def second():\n    return 2\n"}
    return files, python_analysis(files, "v")


def architecture(analysis):
    return Architecture(modules=[Module(id=entity.id, name=entity.name, summary="读取固定数值。",
                        code_paths=[entity.location.path], entity_ids=[entity.id]) for entity in analysis.entities if entity.visible],
                        overview=Overview(introduction="人工数值样例。", capabilities=["返回两种数值"], principle="独立提供数值。", limitations=[]))


@pytest.mark.parametrize("path,start,end", [("../secret.py", 1, 2), ("C:/secret.py", 1, 2), ("a.py", 1, 99), (".env", 1, 1)])
def test_reads_cannot_escape_authorized_snapshot(path, start, end):
    files, analysis = fixture()
    reader = SnapshotTools(analysis, files)
    with pytest.raises(ValueError):
        reader.read([ReadRequest(kind="code", reference={"path": path, "start_line": start, "end_line": end})])
    assert reader.total_chars == 0 and not reader.read_ranges


def test_metadata_whitelist_and_read_size_limits():
    files, analysis = fixture()
    reader = SnapshotTools(analysis, files)
    entity = analysis.entities[0]
    assert reader.read([ReadRequest(kind="entity", ids=[entity.id])])[0]["items"][0]["id"] == entity.id
    with pytest.raises(ValueError):
        reader.read([ReadRequest(kind="confirmed_relations", ids=["imaginary-call"])])
    reader = SnapshotTools(analysis, {"a.py": "x" * 20_001})
    with pytest.raises(ValueError):
        reader.read([ReadRequest(kind="code", reference={"path": "a.py", "start_line": 1, "end_line": 1})])


def test_explanation_own_implementation_and_supplied_ranges_are_required():
    files, analysis = fixture()
    result = architecture(analysis)
    entity = analysis.entities[0]
    note = FunctionExplanation(entity_id=entity.id, summary="返回数值。", details="没有参数，直接返回固定数值。",
                               evidence=[{"path": "a.py", "start_line": 2, "end_line": 2}])
    result.explanations = [note]
    validate_explanations(result, analysis, {entity.id}, files, {"a.py": [(1, 2)]})
    with pytest.raises(ValueError):
        validate_explanations(result, analysis, {entity.id}, files, {"a.py": [(1, 1)]})
    note.evidence[0].path = "b.py"
    with pytest.raises(ValueError, match="自身"):
        validate_explanations(result, analysis, {entity.id}, files, {"b.py": [(1, 2)]})


def test_supplemental_read_and_final_explanation_share_ledger_budget(tmp_path, monkeypatch):
    files, analysis = fixture()
    result = architecture(analysis)
    result.explanations = [FunctionExplanation(entity_id=entity.id, summary="返回固定数值。", details="不需要参数，返回实现中声明的常量。",
        evidence=[{"path": entity.location.path, "start_line": 2, "end_line": 2}]) for entity in analysis.entities]
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        user = json.loads(payload["messages"][1]["content"])
        requests.append(user)
        response = {"read_requests": [{"kind": "code", "reference": {"path": "b.py", "start_line": 1, "end_line": 2}}]} if len(requests) == 1 else result.model_dump()
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}, "finish_reason": "stop"}], "usage": {"total_tokens": 7}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    database = Database(tmp_path)
    provider = Provider(tmp_path, database)
    provider.configure("https://example.invalid/v1", "固定响应验证", "local-test-key")
    saved, usage = asyncio.run(provider.architecture(analysis, files))
    assert len(saved.explanations) == 2 and usage["total_tokens"] == 14
    assert requests[1]["supplemental_reads"][0]["text"] == files["b.py"]
    receipts = RequestLedger(database).summary()["records"]
    assert len({record["unit_id"] for record in receipts}) == 1
    assert sorted(record["attempt"] for record in receipts) == [1, 2]
    assert {record["phase"] for record in receipts} == {"architecture", "source_read"}
    assert "local-test-key" not in json.dumps(receipts)
    database.engine.dispose()


def test_repeated_invalid_reads_and_repeated_truncation_stop(tmp_path, monkeypatch):
    files, analysis = fixture()
    calls = 0
    async def invalid(self, system, user, max_tokens=8000):
        nonlocal calls
        calls += 1
        return {"read_requests": [{"kind": "code", "reference": {"path": "../secret.py", "start_line": 1, "end_line": 1}}]}, {}
    monkeypatch.setattr(Provider, "request", invalid)
    with pytest.raises(ProviderError, match="有限修复"):
        asyncio.run(Provider(tmp_path).architecture(analysis, files))
    assert calls == 3
    calls = 0
    async def truncated(self, system, user, max_tokens=8000):
        nonlocal calls
        calls += 1
        raise ProviderError("模型输出被截断，请缩小分析批次。")
    monkeypatch.setattr(Provider, "request", truncated)
    with pytest.raises(ProviderError, match="单次拆分"):
        asyncio.run(Provider(tmp_path).architecture(analysis, files))
    assert calls == 2


def test_many_functions_plan_smaller_units_without_losing_entities(tmp_path):
    files = {"a.py": "\n".join(f"def f{i}(): return {i}" for i in range(60))}
    analysis = python_analysis(files, "v")
    provider = Provider(tmp_path)
    batches = provider._plan_batches(analysis, files)
    ids = [provider._context(analysis, batch, ranges)[1] for batch, ranges in batches]
    assert len(batches) == 3 and all(len(group) <= 24 for group in ids)
    assert set().union(*ids) == {entity.id for entity in analysis.entities if entity.visible}


def test_function_details_load_prepublished_explanations_without_model(tmp_path, monkeypatch):
    from codescape.app import create_app
    from fastapi.testclient import TestClient
    async def forbidden(*args, **kwargs):
        raise AssertionError("浏览不能请求模型")
    monkeypatch.setattr(Provider, "request", forbidden)
    with TestClient(create_app(tmp_path, "test")) as client:
        client.headers["x-csrf-token"] = client.post("/api/v1/session/exchange", json={"nonce": "test"}).json()["data"]["csrf_token"]
        database = Database(tmp_path)
        project = database.list_projects()[0]
        entity = next(item for item in project["result"]["entities"] if item["visible"])
        arch = Architecture.model_validate(project["architecture"])
        arch.explanations = [FunctionExplanation(entity_id=entity["id"], summary="已完成的中文功能说明。", details="说明来自保存结果。",
            evidence=[{"path": entity["location"]["path"], "start_line": entity["location"]["start_line"], "end_line": entity["location"]["end_line"]}])]
        database.update(project["id"], architecture=arch.model_dump())
        response = client.get(f"/api/v1/projects/{project['id']}").json()["data"]
        found = next(item for item in response["analysis"]["entities"] if item["id"] == entity["id"])
        assert found["explanation"] == "已完成的中文功能说明。" and found["explanation_details"] == "说明来自保存结果。"
        assert found["explanation_evidence"][0]["path"] == entity["location"]["path"]
        file_id = response["file_ids"][entity["location"]["path"]]
        assert client.get(f"/api/v1/projects/{project['id']}/files/{file_id}?version_id={project['version_id']}").status_code == 200
        assert not RequestLedger(database).summary()["records"]
        database.engine.dispose()
