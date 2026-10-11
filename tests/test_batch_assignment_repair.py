import asyncio
import json

import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider


def sample():
    files = {'owned.py': 'def collect(): return 1\ndef present(): return 2\n'}
    analysis = python_analysis(files, 'version')
    value = Architecture(
        modules=[Module(id=str(index), name=entity.name, summary='应用自有固定响应测试职责',
                        entity_ids=[entity.id], code_paths=['owned.py'])
                 for index, entity in enumerate(analysis.entities) if entity.visible],
        overview=Overview(introduction='固定响应测试', capabilities=['只验证归属契约'],
                          principle='读取源码，不执行', limitations=[]))
    return files, analysis, value


@pytest.mark.parametrize('problem', ['missing', 'unknown', 'duplicate'])
def test_one_bounded_repair_recovers_explicit_ownership_without_inventing_mapping(tmp_path, monkeypatch, problem):
    files, analysis, value = sample()
    wrong = value.model_copy(deep=True)
    if problem == 'missing':
        wrong.modules[1].entity_ids = []
    elif problem == 'unknown':
        wrong.modules[1].entity_ids = ['untrusted-model-input']
    else:
        wrong.modules[1].entity_ids.append(wrong.modules[0].entity_ids[0])
    provider = Provider(tmp_path)
    systems = []

    async def fixed(system, context, **kwargs):
        systems.append(system)
        if len(systems) == 1:
            return wrong.model_dump(), {'total_tokens': 5}
        for entity in analysis.entities:
            assert entity.id in system
        assert '恰好分配到一个' in system and 'untrusted-model-input' not in system
        return value.model_dump(), {'total_tokens': 7}

    monkeypatch.setattr(provider, 'request', fixed)
    actual, usage = asyncio.run(provider.architecture(analysis, files))
    assert len(systems) == 2 and usage['total_tokens'] == 12
    assert actual.modules == value.modules and not any(module.incomplete for module in actual.modules)
    assert all(not item.startswith('本批有') for item in actual.overview.limitations)
    assert all('untrusted-model-input' not in item
               for item in (tmp_path / 'validation-errors.jsonl').read_text(encoding='utf-8').splitlines())


def test_repeated_missing_assignment_stays_explicitly_incomplete_and_does_not_retry_forever(tmp_path, monkeypatch):
    files, analysis, value = sample()
    wrong = value.model_copy(deep=True)
    missing = wrong.modules.pop().entity_ids[0]
    provider = Provider(tmp_path)
    count = 0

    async def fixed(*args, **kwargs):
        nonlocal count
        count += 1
        return wrong.model_dump(), {}

    monkeypatch.setattr(provider, 'request', fixed)
    actual, _ = asyncio.run(provider.architecture(analysis, files))
    assert count == 2
    pending = next(module for module in actual.modules if missing in module.entity_ids)
    assert pending.incomplete and pending.name == '待复核模块'
    assert {identifier for module in actual.modules for identifier in module.entity_ids} == {
        entity.id for entity in analysis.entities if entity.visible}
    assert any('未被模型明确归类' in item for item in actual.overview.limitations)
    assert len([json.loads(line) for line in (tmp_path / 'validation-errors.jsonl').read_text(encoding='utf-8').splitlines()]) == 1


def test_valid_assignment_needs_no_extra_request(tmp_path, monkeypatch):
    files, analysis, value = sample()
    provider = Provider(tmp_path)
    count = 0

    async def fixed(*args, **kwargs):
        nonlocal count
        count += 1
        return value.model_dump(), {}

    monkeypatch.setattr(provider, 'request', fixed)
    actual, _ = asyncio.run(provider.architecture(analysis, files))
    assert count == 1 and actual.modules == value.modules


def test_each_batch_has_its_own_single_assignment_repair(tmp_path, monkeypatch):
    files = {'first.py': 'def collect(): return 1\n', 'second.py': 'def present(): return 2\n'}
    analysis = python_analysis(files, 'version')
    provider = Provider(tmp_path)
    monkeypatch.setattr(provider, '_plan_batches', lambda *args: [
        ({path: text}, {path: (1, 1)}) for path, text in files.items()])
    counts = {}

    async def fixed(system, context, **kwargs):
        entities = json.loads(context)['entities']
        assert len(entities) == 1
        entity = entities[0]
        path = entity['location']['path']
        counts[path] = counts.get(path, 0) + 1
        value = Architecture(modules=[Module(id='m1', name=entity['name'], summary='有依据的本批职责',
                            code_paths=[path], entity_ids=[entity['id']] if counts[path] == 2 else [])],
                            overview=Overview(introduction='固定响应测试', capabilities=['本批功能'],
                                              principle='只读取源码', limitations=[]))
        return value.model_dump(), {}

    async def overview(value, index):
        return value.overview, {}

    monkeypatch.setattr(provider, 'request', fixed)
    monkeypatch.setattr(provider, 'project_overview', overview)
    actual, _ = asyncio.run(provider.architecture(analysis, files))
    assert counts == {'first.py': 2, 'second.py': 2}
    assert not any(module.incomplete for module in actual.modules)
    assert {identifier for module in actual.modules for identifier in module.entity_ids} == {
        entity.id for entity in analysis.entities if entity.visible}
