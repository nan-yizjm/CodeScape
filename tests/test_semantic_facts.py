import asyncio
import json

import httpx
import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.provider import Provider
from codescape.semantic_facts import recursive_fold_facts, validate_fold_wording

SOURCE = '''def count(node):
    children = node.get("children", [])
    if not children:
        return 1
    total = 0
    for child in children:
        total += count(child)
    return total
'''


def result(files):
    analysis = python_analysis(files, 'v')
    entity = next(item for item in analysis.entities if item.visible)
    architecture = Architecture(modules=[Module(id='m',name='递归统计',summary='统计子项',entity_ids=[entity.id],code_paths=['count.py'])],
        overview=Overview(introduction='预置统计样例',capabilities=['统计'],principle='递归累加',limitations=[]),
        explanations=[FunctionExplanation(entity_id=entity.id,summary='包含当前节点在内的子树全部节点数。',
            details='从零累加每个子节点的递归返回值。',evidence=[{'path':'count.py','start_line':1,'end_line':8}])])
    return analysis, architecture


def test_recursive_fold_distinguishes_zero_seed_and_one_seed_without_execution():
    analysis, architecture = result({'count.py':SOURCE})
    facts = recursive_fold_facts(analysis, {'count.py':SOURCE})
    assert next(iter(facts.values()))['nonempty_seed'] == 0
    with pytest.raises(ValueError,match='事实冲突'):
        validate_fold_wording(architecture,facts)
    architecture.explanations[0].summary='只统计叶节点，不是全部节点数量。'
    validate_fold_wording(architecture,facts)
    architecture.explanations[0].details='因此返回值是子树全部节点数，而非叶节点数。'
    with pytest.raises(ValueError,match='事实冲突'):
        validate_fold_wording(architecture,facts)
    architecture.explanations[0].details='没有另加当前节点。'
    architecture.overview.analysis_notes=['count 返回包含当前节点在内的全部节点数，而非叶节点数。']
    with pytest.raises(ValueError,match='事实冲突'):
        validate_fold_wording(architecture,facts)
    files = {'count.py':SOURCE.replace('total = 0','total = 1')}
    other = recursive_fold_facts(python_analysis(files,'v'), files)
    assert next(iter(other.values()))['interpretation'] == '在无环有限子项树中计数全部项。'


def test_unsupported_mutation_and_shadowed_recursion_do_not_generate_facts():
    for text in (SOURCE.replace('total += count(child)','total += count(child)\n        total += 1'),
                 SOURCE.replace('def count(node):','def count(node, count):'),
                 SOURCE.replace('for child in children:','for total in children:').replace('count(child)','count(total)')):
        files = {'count.py':text}
        assert not recursive_fold_facts(python_analysis(files,'v'),files)


def test_semantic_failure_repairs_with_source_fact_and_shares_send_budget(tmp_path, monkeypatch):
    files = {'count.py':SOURCE}
    analysis, architecture = result(files)
    requests=[]
    def handle(request):
        payload=json.loads(request.content)
        requests.append(payload)
        document=architecture.model_dump()
        if len(requests) > 1:
            document['explanations'][0]['summary']='递归累加叶节点数量，非叶节点不另计数。'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(document)},'finish_reason':'stop'}],
                                      'usage':{'total_tokens':5}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    provider=Provider(tmp_path)
    provider.configure('https://example.invalid','固定验证','local-test-key')
    saved,usage=asyncio.run(provider.architecture(analysis,files))
    assert len(requests) == 2 and usage['total_tokens'] == 10
    assert json.loads(requests[0]['messages'][1]['content'])['source_facts'][0]['nonempty_seed'] == 0
    assert '事实冲突' in requests[1]['messages'][0]['content']
    assert '叶节点' in saved.explanations[0].summary
