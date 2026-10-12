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


def test_long_function_evidence_reports_span_limit_and_accepts_split_references():
    from codescape.provider import validation_feedback
    files = {'owned.py': 'def large():\n' + '    value = 1\n' * 410 + '    return value\n'}
    analysis = python_analysis(files, 'v')
    entity = next(item for item in analysis.entities if item.visible)
    result = architecture(analysis)
    note = FunctionExplanation(entity_id=entity.id, summary='返回赋值结果。', details='重复赋值后返回结果。',
        evidence=[{'path': 'owned.py', 'start_line': 1, 'end_line': 412}])
    result.explanations = [note]
    with pytest.raises(ValueError, match='最多200行') as error:
        validate_explanations(result, analysis, {entity.id}, files, {'owned.py': [(1, 412)]})
    assert '请拆为多个' in validation_feedback(error.value)
    note.evidence = [type(note.evidence[0])(path='owned.py', start_line=start, end_line=end)
                     for start, end in [(1, 200), (201, 400), (401, 412)]]
    validate_explanations(result, analysis, {entity.id}, files, {'owned.py': [(1, 412)]})
    note.evidence[0].end_line = 201
    with pytest.raises(ValueError, match='最多200行'):
        validate_explanations(result, analysis, {entity.id}, files, {'owned.py': [(1, 412)]})


def test_file_surrounding_function_evidence_is_repaired_with_explicit_bounds(tmp_path, monkeypatch):
    files = {'owned.py': '# private-source-marker\n' + '\n' * 64 + 'def short():\n    value = 1\n    return value\n' + '\n' * 22}
    analysis = python_analysis(files, 'v')
    entity = next(item for item in analysis.entities if item.visible)
    result = architecture(analysis)
    result.explanations = [FunctionExplanation(entity_id=entity.id, summary='返回数值。',
        details='赋值后返回数值。', evidence=[{'path': 'owned.py', 'start_line': 1, 'end_line': 90}])]
    received = []
    def handle(request):
        payload = json.loads(request.content)
        received.append(payload)
        if len(received) == 2:
            feedback = payload['messages'][0]['content']
            assert '必须都落在 implementation_lines 内' in feedback
            assert '[66, 68]' in feedback and '整文件引用' in feedback
            assert 'private-source-marker' not in feedback
            result.explanations[0].evidence[0].start_line = 66
            result.explanations[0].evidence[0].end_line = 68
        return httpx.Response(200, json={'choices': [{'message': {'content': result.model_dump_json()}}],
                                        'usage': {'total_tokens': 7}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    database = Database(tmp_path)
    provider = Provider(tmp_path, database)
    provider.configure('https://example.invalid/v1', '固定响应验证', 'local-test-key')
    saved, usage = asyncio.run(provider.architecture(analysis, files))
    assert len(received) == 2 and usage['total_tokens'] == 14
    assert saved.explanations[0].evidence[0].start_line == 66
    receipts = RequestLedger(database).summary()['records']
    assert {row['status'] for row in receipts} == {'success', 'failed'}
    database.engine.dispose()


@pytest.mark.parametrize('read_first', [True, False])
def test_other_authorized_file_evidence_requires_actual_supplemental_read(tmp_path, monkeypatch, read_first):
    files = {'a.py': 'def first():\n    return ANSWER\n', 'constants.py': 'ANSWER = 42\n'}
    analysis = python_analysis(files, 'v')
    entity = next(item for item in analysis.entities if item.visible)
    result = architecture(analysis)
    result.explanations = [FunctionExplanation(entity_id=entity.id, summary='返回共享数值。',
        details='返回共享常量的值。', evidence=[{'path': 'a.py', 'start_line': 2, 'end_line': 2},
                                              {'path': 'constants.py', 'start_line': 1, 'end_line': 1}])]
    calls = []
    async def request(self, system, user, max_tokens=8000):
        payload = json.loads(user)
        calls.append(payload)
        assert {e['id'] for e in payload['entities']} == {entity.id}
        if read_first and len(calls) == 1:
            return {'read_requests': [{'kind': 'code', 'reference': {
                'path': 'constants.py', 'start_line': 1, 'end_line': 1}}]}, {}
        if read_first:
            assert payload['supplemental_reads'][0]['text'] == files['constants.py']
        return result.model_dump(), {}
    monkeypatch.setattr(Provider, 'request', request)
    monkeypatch.setattr(Provider, '_plan_batches', lambda self, a, f: [({'a.py': f['a.py']}, {'a.py': (1, 2)})])
    provider = Provider(tmp_path)
    if read_first:
        saved, _ = asyncio.run(provider.architecture(analysis, files))
        assert len(calls) == 2 and saved.modules[0].code_paths == ['a.py']
        assert saved.modules[0].entity_ids == [entity.id]
    else:
        with pytest.raises(ProviderError, match='结构校验'):
            asyncio.run(provider.architecture(analysis, files))
        assert len(calls) == 3


def test_checkpoint_batch_merge_preserves_validated_other_file_evidence(tmp_path):
    files = {'a.py': 'def first():\n    return 1\ndef second():\n    return 2\n',
             'constants.py': 'ANSWER = 42\n'}
    analysis = python_analysis(files, 'v')
    parts = []
    for entity in analysis.entities:
        if not entity.visible:
            continue
        result = Architecture(modules=[Module(id=entity.id, name=entity.name, summary='独立返回数值。',
            entity_ids=[entity.id], code_paths=['a.py'])], overview=architecture(analysis).overview)
        result.explanations = [FunctionExplanation(entity_id=entity.id, summary='返回数值。', details='返回固定数值。',
            evidence=[{'path': 'a.py', 'start_line': entity.location.start_line, 'end_line': entity.location.end_line},
                      {'path': 'constants.py', 'start_line': 1, 'end_line': 1}])]
        parts.append(result)
    # 检查点原批次已校验补读；新规划将两批合并，不能丢弃原证据或扩张模块归属。
    saved = Provider(tmp_path)._align_completed_batches(parts,
        [({'a.py': files['a.py']}, {'a.py': (1, 4)})], analysis, set(files))
    assert len(saved) == 1 and len(saved[0].explanations) == 2
    assert all(module.code_paths == ['a.py'] for module in saved[0].modules)
    assert all(note.evidence[1].path == 'constants.py' for note in saved[0].explanations)


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
    assert len(batches) == 5 and all(len(group) <= 12 for group in ids)
    assert set().union(*ids) == {entity.id for entity in analysis.entities if entity.visible}


def test_unnameable_call_does_not_collect_every_anonymous_candidate(tmp_path):
    from codescape.models import Call, Entity, Location, Scope
    analysis = python_analysis({'local.py':'def visible(): return 1'},'v')
    location = Location(path='other.py',start_line=1,end_line=1,start_byte=0,end_byte=20)
    analysis.scopes.append(Scope(id='hidden-scope',kind='lambda',name='<anonymous>',location=location))
    analysis.entities.append(Entity(id='hidden',name='<anonymous>',qualified_name='<anonymous>',signature='lambda x:',
        language='python',location=location,scope_id='hidden-scope',visible=False,hidden_reason='匿名函数',implementation_hash='fixed'))
    analysis.calls.append(Call(id='computed',caller_scope_id=analysis.scopes[0].id,expression='callbacks[index]',
        location=Location(path='local.py',start_line=1,end_line=1,start_byte=0,end_byte=15),resolution='unresolved',origin='static'))
    text,_ = Provider(tmp_path)._context(analysis,{'local.py':'def visible(): return 1'})
    assert json.loads(text)['candidate_entities']==[]
    assert len(json.loads(text)['unresolved_calls'])==1


def test_line_batch_keeps_local_and_incoming_calls_without_unrelated_file_tail(tmp_path):
    from codescape.models import Diagnostic
    files = {'a.py':'def first(): return 1\ndef second(): return first()\ndef third(): return second()\n'}
    analysis = python_analysis(files,'v')
    analysis.diagnostics.extend([Diagnostic(category='unresolved_call',path='a.py',line=3,message='文件尾部调用'),
        Diagnostic(category='configuration',path='a.py',message='全文件配置边界')])
    text,ids = Provider(tmp_path)._context(analysis,{'a.py':files['a.py'].splitlines()[0]}, {'a.py':(1,1)})
    context = json.loads(text)
    assert len(ids)==1
    assert [call['location']['start_line'] for call in context['calls']]==[2]
    assert [item['category'] for item in context['diagnostics']]==['configuration']


def test_relation_heavy_units_refine_whole_functions_and_preserve_source(tmp_path, monkeypatch):
    import codescape.provider as implementation
    files = {'a.py': '\n'.join(f'def f{i}(): return {i}' for i in range(12)) + '\n'}
    analysis = python_analysis(files, 'v')
    monkeypatch.setattr(implementation, 'BATCH_TARGET_CHARS', 6000)
    monkeypatch.setattr(implementation, 'MAX_CONTEXT_CHARS', 8000)
    provider = Provider(tmp_path)
    batches = provider._plan_batches(analysis, files)
    assert len(batches) > 1
    assert ''.join(batch['a.py'] for batch, _ in batches) == files['a.py']
    assigned = []
    for batch, ranges in batches:
        context, identifiers = provider._context(analysis, batch, ranges)
        assert len(context) <= 8000
        assigned.extend(identifiers)
    assert len(assigned) == len(set(assigned)) == 12


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
