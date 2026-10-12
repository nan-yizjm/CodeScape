import asyncio
import json

import httpx
import pytest
from codescape.database import Database
from codescape.ledger import RequestLedger
from codescape.provider import Provider, ProviderError


class Bytes(httpx.AsyncByteStream):
    def __init__(self, value, interrupt=False):
        self.value, self.interrupt, self.closed = value, interrupt, False

    async def __aiter__(self):
        for byte in self.value:
            yield bytes([byte])
        if self.interrupt:
            raise httpx.ReadError('private-transport-marker')

    async def aclose(self):
        self.closed = True


def events(content='{"结果":"成功"}', reason='stop', usage=True, done=True):
    values = [{'choices': [{'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': 'private-thought'}}]}]
    values += [{'choices': [{'index': 0, 'delta': {'content': piece}}]} for piece in (content[:4], content[4:])]
    values.append({'choices': [{'index': 0, 'delta': {}, 'finish_reason': reason}]})
    if usage:
        values.append({'choices': [], 'usage': {'total_tokens': 27, 'unsafe': 'private-usage'}})
    raw = '\ufeff: keepalive\r\n\r\n'+''.join('data: '+json.dumps(v, ensure_ascii=False)+'\r\n\r\n' for v in values)
    return (raw+('data: [DONE]\r\n\r\n' if done else '')).encode()


def setup(tmp_path, monkeypatch, handler):
    db = Database(tmp_path)
    provider = Provider(tmp_path, db)
    provider.configure('https://example.invalid/v1', '固定流式响应', 'local-test-key', True)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    async def sleep(_):
        pass
    monkeypatch.setattr('codescape.provider.asyncio.sleep', sleep)
    return db, provider, RequestLedger(db)


def test_fragmented_utf8_crlf_reasoning_and_final_usage(tmp_path, monkeypatch):
    stream = Bytes(events())
    def handler(request):
        payload = json.loads(request.content)
        assert payload['stream'] is True and payload['stream_options'] == {'include_usage': True}
        return httpx.Response(200, headers={'content-type': 'text/event-stream; charset=utf-8'}, stream=stream)
    db, provider, ledger = setup(tmp_path, monkeypatch, handler)
    value, usage = asyncio.run(provider.request('固定验证', '非源码'))
    assert value == {'结果': '成功'} and usage == {'total_tokens': 27} and stream.closed
    records = ledger.summary()
    assert records['request_count'] == 1 and records['records'][0]['status'] == 'success'
    assert 'private-' not in json.dumps(records)
    db.engine.dispose()


@pytest.mark.parametrize('interrupt', [True, False])
def test_partial_json_is_not_success_even_with_finish_reason_and_usage(tmp_path, monkeypatch, interrupt):
    streams = []
    def handler(_):
        stream = Bytes(events(done=False), interrupt)
        streams.append(stream)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
    db, provider, ledger = setup(tmp_path, monkeypatch, handler)
    with pytest.raises(ProviderError, match='远端用量'):
        asyncio.run(provider.request('固定验证', '非源码'))
    records = ledger.summary()['records']
    assert len(records) == 3 and all(v['status'] == 'outcome_unknown' for v in records)
    assert all(v['usage'] == {'total_tokens': 27} for v in records) and all(s.closed for s in streams)
    db.engine.dispose()


@pytest.mark.parametrize('value', [b'data: private-response\n\n', b'data: [DONE]\n\n',
    b'data: {"choices":[{"delta":{"content":42}}]}\n\n', b'data: \xff\n\n'])
def test_invalid_stream_is_sanitized_and_not_retried(tmp_path, monkeypatch, value):
    db, provider, ledger = setup(tmp_path, monkeypatch, lambda _:
        httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes(value)))
    with pytest.raises(ProviderError, match='未发布部分结果') as error:
        asyncio.run(provider.request('固定验证', '非源码'))
    assert 'private-' not in str(error.value)
    record = ledger.summary()['records'][0]
    assert ledger.summary()['request_count'] == 1 and record['error_code'] == 'INVALID_STREAM'
    db.engine.dispose()


def test_multiline_events_missing_usage_and_content_limit(tmp_path, monkeypatch):
    raw = b'data: {"choices":\ndata: [{"delta":{"content":"{}"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    db, provider, ledger = setup(tmp_path, monkeypatch, lambda _:
        httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Bytes(raw)))
    assert asyncio.run(provider.request('固定验证', '非源码')) == ({}, {})
    assert ledger.summary()['unknown_usage_count'] == 1
    monkeypatch.setattr('codescape.streaming.MAX_RESPONSE_BYTES', 25)
    with pytest.raises(ProviderError, match='接收契约'):
        asyncio.run(provider.request('固定验证', '非源码'))
    assert ledger.summary()['records'][0]['error_code'] == 'INVALID_STREAM'
    db.engine.dispose()


@pytest.mark.parametrize('status', [401, 503])
def test_http_errors_keep_original_retry_policy_with_html_body(tmp_path, monkeypatch, status):
    db, provider, ledger = setup(tmp_path, monkeypatch, lambda _: httpx.Response(status, text='private-html'))
    with pytest.raises(ProviderError):
        asyncio.run(provider.request('固定验证', '非源码'))
    assert ledger.summary()['request_count'] == (1 if status == 401 else 3)
    assert all(v['error_code'] == f'HTTP_{status}' for v in ledger.summary()['records'])
    db.engine.dispose()


def test_compatible_json_response_and_truncation_retains_usage(tmp_path, monkeypatch):
    value = {'choices': [{'message': {'content': '{}'}, 'finish_reason': 'length'}], 'usage': {'total_tokens': 33}}
    db, provider, ledger = setup(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=value))
    with pytest.raises(ProviderError, match='截断'):
        asyncio.run(provider.request('固定验证', '非源码'))
    assert ledger.summary()['records'][0]['usage'] == {'total_tokens': 33}
    assert ledger.summary()['request_count'] == 1
    provider.configure('https://example.invalid/v1', '固定流式响应', None)
    assert provider.settings()['stream_response'] is True
    db.engine.dispose()


def test_cancelled_stream_closes_and_records_unknown_once(tmp_path, monkeypatch):
    class Cancelled(Bytes):
        async def __aiter__(self):
            yield b': heartbeat\n\n'
            raise asyncio.CancelledError
    stream = Cancelled(b'')
    db, provider, ledger = setup(tmp_path, monkeypatch, lambda _:
        httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.request('固定验证', '非源码'))
    assert stream.closed and ledger.summary()['request_count'] == 1
    assert ledger.summary()['records'][0]['error_code'] == 'CANCELLED_AFTER_SEND'
    db.engine.dispose()
