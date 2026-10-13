import asyncio
import json

import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.module_organization import ModuleOrganization, apply_organization, organization_context
from codescape.provider import (
    LEGACY_SOURCE_PIPELINE_VERSION,
    PIPELINE_VERSION,
    PREVIOUS_SOURCE_PIPELINE_VERSION,
    SOURCE_PIPELINE_VERSION,
    Provider,
    ProviderError,
    checkpoint_source_generations,
    compatible_source_checkpoint,
    validate_architecture,
)


def fixture():
    files = {'inputs/a.py': 'def collect(): return 1\ndef step(): return collect()\n',
             'outputs/b.py': 'def publish(): return 2\n'}
    analysis = python_analysis(files, 'version')
    modules = []
    explanations = []
    for number, path in enumerate(files, 1):
        entities = [entity for entity in analysis.entities if entity.location.path == path]
        modules.extend([
            Module(id=f'r{number}', name='工具组件' if number == 1 else '工具系统', summary='组织工具职责',
                   code_paths=[path]),
            Module(id=f'l{number}', parent_id=f'r{number}', name='采集' if number == 1 else '发布',
                   summary='采集' if number == 1 else '发布', code_paths=[path],
                   entity_ids=[entity.id for entity in entities], incomplete=number == 2),
        ])
    for entity in analysis.entities:
        explanations.append(FunctionExplanation(entity_id=entity.id, summary=entity.name, details='返回源码中的数值',
            evidence=[{'path': entity.location.path, 'start_line': entity.location.start_line,
                       'end_line': entity.location.end_line}]))
    return Architecture(modules=modules, explanations=explanations, primary_dependencies=[
        {'call_ids': [analysis.calls[0].id], 'reason': '步骤函数依赖采集返回值'}],
        overview=Overview(introduction='自有固定响应测试', capabilities=['采集', '发布'],
                          principle='按源码职责组织', limitations=['未实跑代码'])), analysis, files


def plan():
    return {'modules': [
        {'id': 'root', 'parent_id': None, 'name': '工具组件', 'summary': '组织采集与发布职责',
         'source_module_ids': ['r1', 'r2'], 'merge_reason': '同职责父容器'},
        {'id': 'collect', 'parent_id': 'root', 'name': '采集', 'summary': '采集输入',
         'source_module_ids': ['l1'], 'merge_reason': '保留采集职责'},
        {'id': 'publish', 'parent_id': 'root', 'name': '发布', 'summary': '发布输出',
         'source_module_ids': ['l2'], 'merge_reason': '保留不同职责'},
    ]}


def test_cross_directory_grouping_preserves_source_entities_notes_and_missing_flags():
    architecture, analysis, files = fixture()
    original = architecture.model_dump()
    organized = apply_organization(architecture, ModuleOrganization.model_validate(plan()))
    validate_architecture(organized, analysis, set(files))
    assert len(organized.modules) == 3 and len([m for m in organized.modules if not m.parent_id]) == 1
    assert set(organized.modules[0].code_paths) == set(files)
    assert organized.modules[0].incomplete and organized.modules[2].incomplete
    assert architecture.model_dump() == original
    assert organized.explanations == architecture.explanations
    assert organized.call_judgments == architecture.call_judgments
    assert organized.primary_dependencies == architecture.primary_dependencies


@pytest.mark.parametrize('kind', ['missing', 'repeated', 'unknown', 'cycle', 'unknown_parent', 'function_parent', 'empty'])
def test_invalid_grouping_is_rejected_without_mutating_original(kind):
    architecture, _, _ = fixture()
    before = architecture.model_dump()
    document = plan()
    if kind == 'missing':
        document['modules'][0]['source_module_ids'] = ['r1']
    elif kind == 'repeated':
        document['modules'][1]['source_module_ids'].append('r1')
    elif kind == 'unknown':
        document['modules'][0]['source_module_ids'].append('unprovided')
    elif kind == 'cycle':
        document['modules'][0]['parent_id'] = 'publish'
    elif kind == 'unknown_parent':
        document['modules'][1]['parent_id'] = 'missing-parent'
    elif kind == 'function_parent':
        document['modules'][2]['parent_id'] = 'collect'
    else:
        document['modules'].append({'id': 'empty', 'parent_id': None, 'name': '空节点', 'summary': '无依据',
                                    'source_module_ids': [], 'merge_reason': '不应建立空节点'})
    with pytest.raises(ValueError):
        apply_organization(architecture, ModuleOrganization.model_validate(document))
    assert architecture.model_dump() == before


def test_organization_context_keeps_all_modules_and_marks_limited_function_hints():
    architecture, analysis, _ = fixture()
    context = organization_context(architecture, analysis)
    data = json.loads(context)
    assert {m['id'] for m in data['modules']} == {m.id for m in architecture.modules}
    assert all(m['omitted_function_hint_count'] == 0 for m in data['modules'])
    assert 'def collect' not in context and 'def publish' not in context
    assert data['confirmed_collaboration'] == []


def test_provider_organization_uses_bounded_contract_and_does_not_change_old_result(tmp_path, monkeypatch):
    architecture, analysis, _ = fixture()
    before = architecture.model_dump()

    async def response(self, system, user, max_tokens):
        assert '每个原模块ID' in system and max_tokens == 16000
        assert {m['id'] for m in json.loads(user)['modules']} == {'r1', 'r2', 'l1', 'l2'}
        return plan(), {'total_tokens': 12}

    monkeypatch.setattr(Provider, 'request', response)
    organized, usage = asyncio.run(Provider(tmp_path).organize_modules(architecture, analysis))
    assert len(organized.modules) == 3 and usage == {'total_tokens': 12}
    assert architecture.model_dump() == before


def test_failed_organization_keeps_source_result_and_stops_after_finite_repairs(tmp_path, monkeypatch):
    architecture, analysis, _ = fixture()
    before = architecture.model_dump()
    calls = []

    async def response(self, system, user, max_tokens):
        calls.append(system)
        invalid = plan()
        invalid['modules'][0]['source_module_ids'] = []
        return invalid, {}

    monkeypatch.setattr(Provider, 'request', response)
    with pytest.raises(ProviderError, match='全局职责整理未完成'):
        asyncio.run(Provider(tmp_path).organize_modules(architecture, analysis))
    assert len(calls) == 3 and architecture.model_dump() == before


@pytest.mark.parametrize('saved,expected', [
    ({'pipeline_version': LEGACY_SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': LEGACY_SOURCE_PIPELINE_VERSION + '-organization/1',
      'source_pipeline_version': LEGACY_SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': PREVIOUS_SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': PREVIOUS_SOURCE_PIPELINE_VERSION + '-organization/1',
      'source_pipeline_version': PREVIOUS_SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': PIPELINE_VERSION, 'source_pipeline_version': PREVIOUS_SOURCE_PIPELINE_VERSION}, False),
    ({'pipeline_version': SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': PIPELINE_VERSION, 'source_pipeline_version': SOURCE_PIPELINE_VERSION}, True),
    ({'pipeline_version': PIPELINE_VERSION}, True),
    ({'pipeline_version': SOURCE_PIPELINE_VERSION + '-organization/1'}, True),
    ({'pipeline_version': SOURCE_PIPELINE_VERSION + '-organization/1-overview-guidance/999'}, False),
    ({'pipeline_version': SOURCE_PIPELINE_VERSION + '-overview-guidance/1'}, False),
    ({'pipeline_version': 'older-source-version'}, False),
    ({'pipeline_version': PIPELINE_VERSION, 'source_pipeline_version': 'different-source-generation'}, False),
    ({'pipeline_version': 'unknown-final-version', 'source_pipeline_version': SOURCE_PIPELINE_VERSION}, False),
])
def test_only_known_source_contract_checkpoints_can_enter_strict_revalidation(saved, expected):
    assert compatible_source_checkpoint(saved) is expected


def test_reused_generation_origins_are_preserved_and_invalid_origins_stop_recovery():
    saved = {'pipeline_version': PREVIOUS_SOURCE_PIPELINE_VERSION + '-organization/1'}
    assert checkpoint_source_generations(saved) == {PREVIOUS_SOURCE_PIPELINE_VERSION}
    saved.update(pipeline_version=PIPELINE_VERSION,
                 source_generation_versions=[PREVIOUS_SOURCE_PIPELINE_VERSION, SOURCE_PIPELINE_VERSION])
    assert checkpoint_source_generations(saved) == {PREVIOUS_SOURCE_PIPELINE_VERSION, SOURCE_PIPELINE_VERSION}
    for wrong in ([], 'unknown', [None], ['not-a-known-version']):
        saved['source_generation_versions'] = wrong
        with pytest.raises(ProviderError, match='生成来源版本无效'):
            checkpoint_source_generations(saved)
