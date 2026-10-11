import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from codescape.app import create_app
from codescape.database import Database
from codescape.ledger import RequestLedger, ai_scope
from codescape.provider import Provider, ProviderError, retry_after
from fastapi.testclient import TestClient


def configured(tmp_path, monkeypatch, handler):
    database = Database(tmp_path)
    provider = Provider(tmp_path, database)
    provider.configure("https://example.invalid/v1", "固定响应验证", "local-test-key")
    # A scoped request must represent an active task and a concrete consent.
    database.insert({"id": "p", "name": "固定响应账本验证", "source": "fixture", "created_at": "now",
                     "status": "indexed", "snapshot": {"hash": "fixture-hash", "files": {}}, "version_id": "v", "demo": 0})
    database.insert_job({"id": "j", "project_id": "p", "kind": "architecture", "status": "running",
                         "version_id": "v", "position": 0, "created_at": "now", "updated_at": "now", "detail": "固定响应验证"})
    (tmp_path / "consents.json").write_text(json.dumps({"p:v": {
        "base_url": "https://example.invalid/v1", "snapshot_hash": "fixture-hash"}}), encoding="utf-8")
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return database, provider, RequestLedger(database)


def document(content='{"connected":true}', usage=None):
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], **({"usage": usage} if usage is not None else {})}


@pytest.mark.parametrize('endpoint,model,thinking',[
    ('https://api.deepseek.com','deepseek-flash',True),
    ('https://api.deepseek.com/v1','deepseek-flash',True),
    ('https://example.invalid/v1','deepseek-flash',False),
    ('https://api.deepseek.com','compatible-other',False)])
def test_official_flash_reserves_thinking_budget_without_affecting_compatible_endpoints(tmp_path,monkeypatch,endpoint,model,thinking):
    captured = []
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json=document())
    database,provider,_ = configured(tmp_path,monkeypatch,handler)
    provider.configure(endpoint,model,None)
    asyncio.run(provider.request('固定 JSON 验证','应用自有固定响应',8000))
    payload = captured[0]
    assert payload['max_tokens']==(32768 if thinking else 8000)
    assert (payload.get('thinking'),payload.get('reasoning_effort')) == (
        ({'type':'enabled'},'high') if thinking else (None,None))
    database.engine.dispose()


def test_receipt_has_scope_and_known_usage_but_no_sensitive_payload(tmp_path, monkeypatch):
    database, provider, ledger = configured(tmp_path, monkeypatch, lambda request:
        httpx.Response(200, json=document(usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15, "unsafe": "secret"})))
    with ai_scope(project_id="p", version_id="v", job_id="j", phase="architecture", source_chars=24):
        result, usage = asyncio.run(provider.request("secret-system", "private-source-marker"))
    assert result == {"connected": True} and usage["total_tokens"] == 15
    summary = ledger.summary("p")
    record = summary["records"][0]
    assert (record["project_id"], record["version_id"], record["job_id"], record["phase"]) == ("p", "v", "j", "architecture")
    assert summary["total_tokens"] == 15 and summary["unknown_usage_count"] == 0
    assert summary["cost"] is None and record["cost"] is None and record["price_snapshot"] is None
    assert record["sent_at"] and record["finished_at"] and record["source_chars"] == 24
    stored = json.dumps(summary)
    assert all(value not in stored for value in ("private-source-marker", "secret-system", "local-test-key", "unsafe"))
    database.engine.dispose()


@pytest.mark.parametrize("usage,expected", [(None, None), ({"total_tokens": 0}, 0), ({"total_tokens": True}, None)])
def test_missing_usage_differs_from_known_zero(tmp_path, monkeypatch, usage, expected):
    database, provider, ledger = configured(tmp_path, monkeypatch, lambda request: httpx.Response(200, json=document(usage=usage)))
    asyncio.run(provider.test())
    assert ledger.summary()["total_tokens"] == expected
    assert ledger.summary()["unknown_usage_count"] == (expected is None)
    database.engine.dispose()


def test_invalid_json_retains_returned_usage_and_sanitizes_errors(tmp_path, monkeypatch):
    database, provider, ledger = configured(tmp_path, monkeypatch, lambda request:
        httpx.Response(200, json=document("not-json-private-response", {"total_tokens": 17})))
    with pytest.raises(ProviderError, match="结构化契约"):
        asyncio.run(provider.test())
    record = ledger.summary()["records"][0]
    assert record["status"] == "failed" and record["error_code"] == "INVALID_JSON"
    assert record["usage"]["total_tokens"] == 17 and "private-response" not in json.dumps(record)
    database.engine.dispose()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 302])
def test_non_retryable_failures_have_single_receipt(tmp_path, monkeypatch, status):
    database, provider, ledger = configured(tmp_path, monkeypatch, lambda request:
        httpx.Response(status, json={"error": "private-server-message"}, headers={"location": "https://other.invalid"}))
    with pytest.raises(ProviderError):
        asyncio.run(provider.test())
    summary = ledger.summary()
    assert summary["request_count"] == 1 and summary["records"][0]["error_code"] == f"HTTP_{status}"
    assert summary["total_tokens"] is None and "private-server-message" not in json.dumps(summary)
    database.engine.dispose()


def test_retry_after_and_bounded_attempts(tmp_path, monkeypatch):
    waits, calls = [], []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "7"}) if len(calls) == 1 else httpx.Response(200, json=document())
    async def sleep(delay):
        waits.append(delay)
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    monkeypatch.setattr("codescape.provider.asyncio.sleep", sleep)
    asyncio.run(provider.test())
    assert waits == [7] and ledger.summary()["request_count"] == 2
    records = ledger.summary()["records"]
    assert {item["unit_id"] for item in records} == {records[0]["unit_id"]}
    assert {item["attempt"] for item in records} == {1, 2}
    database.engine.dispose()


def test_long_retry_after_pauses_without_sleep_or_new_request(tmp_path, monkeypatch):
    database, provider, ledger = configured(tmp_path, monkeypatch, lambda request: httpx.Response(429, headers={"retry-after": "61"}))
    with pytest.raises(ProviderError, match="主动恢复"):
        asyncio.run(provider.test())
    assert ledger.summary()["request_count"] == 1
    assert 55 < retry_after(format_datetime(datetime.now(UTC) + timedelta(seconds=60))) <= 60
    assert retry_after("not-a-date") is None and retry_after("nan") is None
    database.engine.dispose()


def test_transport_unknown_records_all_attempts(tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("private-transport-message", request=request)
    async def sleep(delay):
        pass
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    monkeypatch.setattr("codescape.provider.asyncio.sleep", sleep)
    with pytest.raises(ProviderError, match="用量可能不确定"):
        asyncio.run(provider.test())
    summary = ledger.summary()
    assert summary["request_count"] == summary["outcome_unknown_count"] == 3
    assert all(item["status"] == "outcome_unknown" for item in summary["records"])
    assert "private-transport-message" not in json.dumps(summary)
    database.engine.dispose()


def test_cancellation_during_sent_request_is_unknown(tmp_path, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        database, provider, ledger = configured(tmp_path, monkeypatch, handler)
        task = asyncio.create_task(provider.test())
        await entered.wait()
        assert ledger.summary()["records"][0]["status"] == "sent"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ledger.summary()["records"][0]["error_code"] == "CANCELLED_AFTER_SEND"
        database.engine.dispose()
    asyncio.run(scenario())


def test_cancel_during_retry_delay_keeps_known_http_failure(tmp_path, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        async def sleep(delay):
            entered.set()
            await asyncio.Event().wait()
        database, provider, ledger = configured(tmp_path, monkeypatch, lambda request: httpx.Response(429))
        monkeypatch.setattr("codescape.provider.asyncio.sleep", sleep)
        task = asyncio.create_task(provider.test())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = ledger.summary()["records"][0]
        assert record["status"] == "failed" and record["error_code"] == "HTTP_429"
        database.engine.dispose()
    asyncio.run(scenario())


def test_crash_receipts_recover_without_transmission_and_paginate(tmp_path, monkeypatch):
    database = Database(tmp_path)
    ledger = RequestLedger(database)
    for index in range(24):
        identity = ledger.prepare({"base_url": "https://example.invalid", "model": "固定响应"}, {"test": "not-stored"}, "unit", index+1)
        if index % 2:
            ledger.sent(identity)
    database.engine.dispose()
    async def forbidden(*args, **kwargs):
        raise AssertionError("恢复不应发送请求")
    monkeypatch.setattr(Provider, "request", forbidden)
    with TestClient(create_app(tmp_path, "nonce")) as client:
        assert client.get("/api/v1/usage").status_code == 401
        client.post("/api/v1/session/exchange", json={"nonce": "nonce"})
        first = client.get("/api/v1/usage?limit=20").json()["data"]
        second = client.get("/api/v1/usage?offset=20").json()["data"]
        assert first["request_count"] == first["unknown_usage_count"] == 24
        assert first["outcome_unknown_count"] == 12 and first["next_offset"] == 20
        assert len(first["records"]) == 20 and len(second["records"]) == 4
        assert not ({item["id"] for item in first["records"]} & {item["id"] for item in second["records"]})
        assert client.get("/api/v1/usage?limit=101").status_code == 422


def test_structured_repairs_share_budget_and_account_schema_failure(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        content = '{"wrong":1}' if len(calls) == 1 else '{"right":2}'
        return httpx.Response(200, json=document(content, {"total_tokens": 5}))
    def validator(value):
        if "right" not in value:
            raise ValueError("unsafe-field-source")
        return value
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    with ai_scope(phase="architecture"):
        result, usage = asyncio.run(provider.structured("schema", "context", validator))
    assert result == {"right": 2} and usage["total_tokens"] == 10
    records = ledger.summary()["records"]
    assert records[0]["phase"] == "repair" and records[0]["status"] == "success"
    assert records[1]["error_code"] == "SCHEMA_INVALID" and records[1]["usage"]["total_tokens"] == 5
    assert records[0]["unit_id"] == records[1]["unit_id"]
    database.engine.dispose()


def test_transport_and_repairs_never_exceed_six_sends(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) % 3:
            return httpx.Response(503)
        return httpx.Response(200, json=document("not-json", {"total_tokens": 9}))
    async def sleep(delay):
        pass
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    monkeypatch.setattr("codescape.provider.asyncio.sleep", sleep)
    with pytest.raises(ProviderError, match="6 次模型尝试上限"):
        asyncio.run(provider.structured("schema", "context", lambda value: value))
    records = ledger.summary()["records"]
    assert len(calls) == len(records) == 6
    assert {item["attempt"] for item in records} == set(range(1, 7))
    assert ledger.summary()["known_total_tokens"] == 18 and ledger.summary()["total_tokens"] is None
    database.engine.dispose()


def test_project_deletion_removes_only_its_receipts(tmp_path):
    database = Database(tmp_path)
    ledger = RequestLedger(database)
    for project in ("p", "q"):
        database.insert({"id": project, "name": project, "source": "fixture", "created_at": "now",
                         "status": "indexed", "snapshot": {"files": {}}, "version_id": "v", "demo": 0})
        with ai_scope(project_id=project):
            identifier = ledger.prepare({"base_url": "https://example.invalid", "model": "固定响应"}, {}, "unit", 1)
            ledger.finish(identifier, "success", usage={"total_tokens": 2})
    operation = database.begin_delete("p")
    database.finish_delete(operation["id"], "p")
    assert ledger.summary("p")["request_count"] == 0
    assert ledger.summary("q")["total_tokens"] == 2
    database.engine.dispose()


@pytest.mark.parametrize("change,expected", [("pause", "任务已停止"), ("consent", "授权已失效"),
    ("delete", "正在清理"), ("model", "模型配置已改变"), ("version", "版本已失效")])
def test_scope_is_rechecked_before_retry_without_second_upload(tmp_path, monkeypatch, change, expected):
    calls = []
    def handler(request):
        calls.append(request)
        if change == "pause":
            database.update_job("j", status="paused")
        elif change == "consent":
            (tmp_path / "consents.json").write_text("{}", encoding="utf-8")
        elif change == "delete":
            database.begin_delete("p")
        elif change == "model":
            provider.configure("https://example.invalid/v1", "已改变模型", None)
        else:
            database.update("p", version_id="new-version")
        return httpx.Response(429, headers={"retry-after": "0"})
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    with ai_scope(project_id="p", version_id="v", job_id="j", phase="architecture"):
        with pytest.raises(ProviderError, match=expected):
            asyncio.run(provider.request("固定响应", "应用自有样例"))
    assert len(calls) == ledger.summary()["request_count"] == 1
    assert ledger.summary()["records"][0]["error_code"] == "HTTP_429"
    database.engine.dispose()


def test_model_change_between_batches_requires_active_restart(tmp_path, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=document())
    database, provider, ledger = configured(tmp_path, monkeypatch, handler)
    expected = {key: provider.settings()[key] for key in ("base_url", "model")}
    with ai_scope(project_id="p", version_id="v", job_id="j", request_config=expected):
        asyncio.run(provider.request("固定响应", "第一批"))
        provider.configure("https://example.invalid/v1", "另一模型", None)
        with pytest.raises(ProviderError, match="模型配置已改变"):
            asyncio.run(provider.request("固定响应", "第二批"))
    assert len(calls) == ledger.summary()["request_count"] == 1
    database.engine.dispose()
