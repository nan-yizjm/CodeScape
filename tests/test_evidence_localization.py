import asyncio
import json
from pathlib import Path

import pytest
from codescape.analyzers import python_analysis
from codescape.context_tools import validate_explanations
from codescape.evidence_localization import localize_explanation_evidence
from codescape.models import Architecture, CodeReference, FunctionExplanation, Module, Overview
from codescape.provider import Provider, ProviderError


def fixture():
    files = {'a.py': 'flag = 1\ndef process():\n    return flag\n# end\n'}
    analysis = python_analysis(files, 'fixed')
    entity = analysis.entities[0]
    architecture = Architecture(modules=[Module(id='logic', name='处理', summary='返回标志',
        entity_ids=[entity.id], code_paths=['a.py'])],
        overview=Overview(introduction='自有静态样例', capabilities=['返回标志'], principle='读取标志', limitations=['未运行']),
        explanations=[FunctionExplanation(entity_id=entity.id, summary='读取标志', details='从模块读取并返回标志。',
            evidence=[{'path': 'a.py', 'start_line': 1, 'end_line': 4}])])
    return architecture, analysis, files, {entity.id}, {'a.py': [(1, 4)]}


def test_covering_reference_is_narrowed_without_changing_text_or_source_and_original_is_retained():
    architecture, analysis, files, expected, ranges = fixture()
    before = architecture.model_dump()
    result, records = localize_explanation_evidence(architecture, analysis, expected, files, ranges)
    validate_explanations(result, analysis, expected, files, ranges)
    assert architecture.model_dump() == before
    assert result.explanations[0].summary == architecture.explanations[0].summary
    assert result.explanations[0].details == architecture.explanations[0].details
    assert result.modules == architecture.modules and result.call_judgments == architecture.call_judgments
    assert result.primary_dependencies == architecture.primary_dependencies
    assert records == [{'entity_id': analysis.entities[0].id, 'original': {
        'path': 'a.py', 'start_line': 1, 'end_line': 4}, 'localized': {
        'path': 'a.py', 'start_line': 2, 'end_line': 3}, 'origin': 'application_range_localization',
        'action': 'add_implementation_anchor'}]
    assert result.explanations[0].evidence[1:] == architecture.explanations[0].evidence


@pytest.mark.parametrize('problem', ['wrong_file', 'not_provided', 'gap', 'outside_file', 'partial_body',
                                   'unknown_entity', 'hidden_entity', 'over_limit'])
def test_unsafe_references_are_not_guessed_or_repaired(problem):
    architecture, analysis, files, expected, ranges = fixture()
    note = architecture.explanations[0]
    if problem == 'wrong_file':
        files['other.py'] = files['a.py']
        ranges['other.py'] = [(1, 4)]
        note.evidence[0].path = 'other.py'
    elif problem == 'not_provided':
        ranges['a.py'] = [(2, 3)]
    elif problem == 'gap':
        ranges['a.py'] = [(1, 2), (3, 4)]
    elif problem == 'outside_file':
        note.evidence[0].end_line = 5
        ranges['a.py'] = [(1, 5)]
    elif problem == 'partial_body':
        note.evidence[0].end_line = 2
    elif problem == 'unknown_entity':
        note.entity_id = 'unknown'
    elif problem == 'hidden_entity':
        analysis.entities[0].visible = False
    elif problem == 'over_limit':
        files['a.py'] += '# data\n' * 200
        ranges['a.py'] = [(1, 204)]
        note.evidence[0].end_line = 201
    before = architecture.model_dump()
    result, records = localize_explanation_evidence(architecture, analysis, expected, files, ranges)
    assert not records and result.model_dump() == before and architecture.model_dump() == before


def test_specific_reference_is_not_changed_and_no_extra_evidence_is_added():
    architecture, analysis, files, expected, ranges = fixture()
    architecture.explanations[0].evidence.append(CodeReference(path='a.py', start_line=2, end_line=3))
    result, records = localize_explanation_evidence(architecture, analysis, expected, files, ranges)
    assert result == architecture and not records


def test_six_references_are_not_replaced_or_extended_to_hide_a_contract_failure():
    architecture, analysis, files, expected, ranges = fixture()
    ref = architecture.explanations[0].evidence[0]
    architecture.explanations[0].evidence = [ref.model_copy() for _ in range(6)]
    result, records = localize_explanation_evidence(architecture, analysis, expected, files, ranges)
    assert result == architecture and not records
    with pytest.raises(ValueError, match='实现自身'):
        validate_explanations(result, analysis, expected, files, ranges)


def test_localization_audit_survives_restoring_completed_notes_from_first_attempt(tmp_path, monkeypatch):
    files = {'a.py': '# header\ndef first():\n    return 1\ndef second():\n    return 2\n'}
    analysis = python_analysis(files, 'fixed')
    first, second = analysis.entities
    architecture = Architecture(modules=[Module(id='logic', name='常量', summary='两种常量',
        entity_ids=[first.id, second.id], code_paths=['a.py'])],
        overview=Overview(introduction='自有样例', capabilities=['返回常量'], principle='读取', limitations=[]))
    calls = []

    async def response(self, *args, **kwargs):
        calls.append(1)
        entity, start, end = (first, 1, 5) if len(calls) == 1 else (second, 4, 5)
        architecture.explanations = [FunctionExplanation(entity_id=entity.id, summary='返回常量', details='返回',
            evidence=[CodeReference(path='a.py', start_line=start, end_line=end)])]
        return architecture.model_dump(), {'total_tokens': 5}

    monkeypatch.setattr(Provider, 'request', response)
    provider = Provider(tmp_path)
    context, _ = provider._context(analysis, files)
    result, _ = asyncio.run(provider._read_batch('固定响应', context, Architecture.model_validate,
                                                analysis, files, {'a.py': (1, 5)}))
    assert len(calls) == 2 and len(result.explanations) == 2
    audit = json.loads((tmp_path/'reference-localizations.jsonl').read_text(encoding='utf-8'))
    assert audit['adjustments'][0]['entity_id'] == first.id
    assert audit['adjustments'][0]['original']['start_line'] == 1


def test_current_provider_accepts_covering_reference_in_one_request_and_saves_audit(tmp_path, monkeypatch):
    architecture, analysis, files, expected, ranges = fixture()
    provider = Provider(tmp_path)
    context, _ = provider._context(analysis, files)
    requests = []

    async def response(self, system, user, max_tokens, **kwargs):
        requests.append(user)
        return architecture.model_dump(), {'total_tokens': 10}

    monkeypatch.setattr(Provider, 'request', response)
    result, usage = asyncio.run(provider._read_batch('固定响应', context, Architecture.model_validate,
                                                    analysis, files, {'a.py': (1, 4)}))
    assert len(requests) == 1 and usage == {'total_tokens': 10}
    validate_explanations(result, analysis, expected, files, ranges)
    record = json.loads((tmp_path / 'reference-localizations.jsonl').read_text(encoding='utf-8'))
    assert record['adjustments'][0]['original']['start_line'] == 1
    assert record['adjustments'][0]['localized']['start_line'] == 2
    assert any('应用为1条' in item for item in result.overview.analysis_notes)


def test_audit_failure_stops_result_without_additional_model_attempts(tmp_path, monkeypatch):
    architecture, analysis, files, _, _ = fixture()
    provider = Provider(tmp_path)
    context, _ = provider._context(analysis, files)
    original_open = Path.open
    requests = []

    async def response(self, *args, **kwargs):
        requests.append(1)
        return architecture.model_dump(), {}

    def open_file(self, *args, **kwargs):
        if self.name == 'reference-localizations.jsonl':
            raise OSError('固定响应磁盘故障')
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Provider, 'request', response)
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(ProviderError, match='引用定位记录保存失败'):
        asyncio.run(provider._read_batch('固定响应', context, Architecture.model_validate,
                                         analysis, files, {'a.py': (1, 4)}))
    assert requests == [1]


def test_existing_checkpoint_must_pass_strict_revalidation_and_is_not_silently_localized(tmp_path):
    architecture, analysis, files, _, _ = fixture()
    provider = Provider(tmp_path)
    batches = provider._plan_batches(analysis, files)
    with pytest.raises(ProviderError, match='旧源码检查点未通过'):
        provider._align_completed_batches([architecture], batches, analysis, set(files), files=files)
    architecture.explanations[0].evidence[0].start_line = 2
    architecture.explanations[0].evidence[0].end_line = 3
    assert provider._align_completed_batches([architecture], batches, analysis, set(files), files=files) == [architecture]
