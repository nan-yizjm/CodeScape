import asyncio
import json

import httpx
import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.provider import Provider


@pytest.mark.parametrize('completion', ['fill', 'still_missing', 'only_missing'])
def test_partial_explanations_get_one_safe_completion_attempt(tmp_path, monkeypatch, completion):
    files = {'a.py': '# private-source-marker\ndef first():\n    return 1\ndef second():\n    return 2\n'}
    analysis = python_analysis(files, 'v')
    entities = [entity for entity in analysis.entities if entity.visible]
    result = Architecture(modules=[Module(id='m', name='固定数值', summary='提供两种数值。',
        entity_ids=[entity.id for entity in entities], code_paths=['a.py'])],
        overview=Overview(introduction='人工验证样例。', capabilities=['提供数值'], principle='返回常量。', limitations=[]))
    def note(entity):
        return FunctionExplanation(entity_id=entity.id, summary='返回固定数值。', details='无需输入，返回实现中的常量。',
            evidence=[{'path': 'a.py', 'start_line': entity.location.start_line, 'end_line': entity.location.end_line}])
    result.explanations = [note(entities[0])]
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            feedback = payload['messages'][0]['content']
            assert entities[1].id in feedback and '补齐 allowed_entity_ids' in feedback
            assert '不是新的模块归属集合' in feedback and 'private-source-marker' not in feedback
            if completion != 'still_missing':
                if completion == 'only_missing':
                    result.explanations = []
                result.explanations.append(note(entities[1]))
        return httpx.Response(200, json={'choices': [{'message': {'content': result.model_dump_json()}}],
                                        'usage': {'total_tokens': 5}})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    provider = Provider(tmp_path)
    provider.configure('https://example.invalid/v1', '固定响应验证', 'local-test-key')
    saved, usage = asyncio.run(provider.architecture(analysis, files))
    assert len(requests) == 2 and usage['total_tokens'] == 10
    assert len(saved.explanations) == (1 if completion == 'still_missing' else 2)
    assert set(saved.modules[0].entity_ids) == {entity.id for entity in entities}


def test_incomplete_supplied_implementation_does_not_force_missing_explanation(tmp_path, monkeypatch):
    files = {'a.py': 'def first():\n    return 1\n', 'b.py': 'def second():\n    return 2\n'}
    analysis = python_analysis(files, 'v')
    entities = [entity for entity in analysis.entities if entity.visible]
    first = next(entity for entity in entities if entity.name == 'first')
    document = Architecture(modules=[Module(id='m', name='提供数值', summary='返回常量。',
        entity_ids=[entity.id for entity in entities], code_paths=list(files))],
        overview=Overview(introduction='预置样例', capabilities=['数值'], principle='返回常量', limitations=[]),
        explanations=[FunctionExplanation(entity_id=first.id, summary='返回一。', details='返回常量一。',
            evidence=[{'path': 'a.py', 'start_line': 1, 'end_line': 2}])])
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': document.model_dump_json()}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    provider = Provider(tmp_path)
    provider.configure('https://example.invalid/v1', '固定响应验证', 'local-test-key')
    context, _ = provider._context(analysis, {'a.py': files['a.py'], 'b.py': 'def second():\n'},
                                    {'a.py': (1, 2), 'b.py': (1, 1)})
    saved, _ = asyncio.run(provider._read_batch('固定响应', context, Architecture.model_validate,
                                              analysis, files, {'a.py': (1, 2), 'b.py': (1, 1)}))
    assert len(requests) == 1 and len(saved.explanations) == 1
