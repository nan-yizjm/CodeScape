import asyncio
import json
from types import SimpleNamespace

import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview

from scripts import explanation_review as review


def fixture():
    files = {'a.py': 'def write():\n    return 42\n\ndef close():\n    write()\n'}
    analysis = python_analysis(files, 'fixed')
    entities = {entity.name: entity for entity in analysis.entities}
    notes = [FunctionExplanation(entity_id=entity.id, summary=entity.name, details='原固定响应讲解',
                                evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})])
             for entity in entities.values()]
    architecture = Architecture(modules=[Module(id='work', name='测试职责', summary='自有验证', incomplete=True,
        entity_ids=list(entity.id for entity in entities.values()), code_paths=['a.py'])], explanations=notes,
        overview=Overview(introduction='自有固定响应', capabilities=['测试'], principle='测试', limitations=['不运行源码']))
    return architecture, analysis, files, entities


@pytest.mark.parametrize(('language', 'text', 'selected'), [
    ('cpp', 'void close() { tracer_.disable(); }', True),
    ('cpp', 'int query() { return engine.query(value); }', True),
    ('c', 'int query() { return callback(value); }', True),
    ('cpp', 'void close() { if (open) tracer_.disable(); }', False),
    ('cpp', 'void close() { tracer_.disable(); state = 0; }', False),
    ('cpp', 'void close() { tracer_.disable("a"); }', False),
    ('cpp', 'void close() { /* hint */ tracer_.disable(); }', False),
    ('python', 'def close():\n    """说明"""\n    engine.close()\n', True),
    ('python', 'def close():\n    engine.close()\n    state = 0\n', False),
    ('typescript', 'function close() { engine.close(); }', False),
])
def test_selector_is_conservative_syntax_only(language, text, selected):
    assert review.forwarding_body(SimpleNamespace(language=language), text) is selected


def test_review_only_changes_selected_note_and_keeps_architecture_and_evidence_boundaries():
    architecture, analysis, files, entities = fixture()
    before = architecture.model_dump()
    context, ranges, expected = review.review_context(architecture, analysis, files)
    assert expected == {entities['close'].id}
    assert [card['entity_id'] for card in json.loads(context)['functions']] == list(expected)
    close = entities['close']
    note = FunctionExplanation(entity_id=close.id, summary='委托关闭', details='仅执行委托，不推断内部资源恢复。',
                              evidence=[{'path': 'a.py', 'start_line': 4, 'end_line': 5}])
    plan = review.ExplanationReview(items=[{'entity_id': close.id, 'decision': 'corrected',
                                         'reason': '删除无依据副作用', 'explanation': note}])
    result = review.apply_review(architecture, plan, analysis, files, ranges, expected)
    assert architecture.model_dump() == before
    assert result.modules == architecture.modules and result.modules[0].incomplete
    assert result.overview == architecture.overview and result.call_judgments == architecture.call_judgments
    assert result.primary_dependencies == architecture.primary_dependencies
    assert result.explanations[0] == architecture.explanations[0] and result.explanations[1] == note


def test_review_rejects_omissions_duplicates_new_entities_and_unprovided_citation():
    architecture, analysis, files, entities = fixture()
    _, ranges, expected = review.review_context(architecture, analysis, files)
    item = {'entity_id': entities['close'].id, 'decision': 'unchanged', 'reason': '准确'}
    for items in ([], [item, item], [{**item, 'entity_id': entities['write'].id}]):
        with pytest.raises(ValueError, match='恰好出现一次'):
            review.apply_review(architecture, review.ExplanationReview(items=items), analysis, files, ranges, expected)
    item = {**item, 'decision': 'corrected', 'explanation': {
        'entity_id': entities['close'].id, 'summary': '测试', 'details': '测试',
        'evidence': [{'path': 'a.py', 'start_line': 1, 'end_line': 2}]}}
    with pytest.raises(ValueError):
        review.apply_review(architecture, review.ExplanationReview(items=[item]), analysis, files, ranges, expected)


def test_review_batches_do_not_drop_functions_and_bound_each_context():
    architecture, analysis, files, _ = fixture()
    batches = review.review_batches(architecture, analysis, files, max_entities=1)
    assert len(batches) == 1 and len(batches[0][2]) == 1
    assert all(len(context) <= 150_000 for context, _, _ in batches)
    with pytest.raises(ValueError, match='超过保护上限'):
        review.review_batches(architecture, analysis, files, max_chars=10)


def test_review_splits_large_selected_set_without_repetition():
    architecture, _, _, _ = fixture()
    files = {'a.py': 'def write():\n    return 1\n' + ''.join(
        f'\ndef wrap{number}():\n    return write()\n' for number in range(13))}
    analysis = python_analysis(files, 'fixed')
    architecture.explanations = [FunctionExplanation(entity_id=entity.id, summary=entity.name, details='固定响应',
        evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})])
        for entity in analysis.entities]
    batches = review.review_batches(architecture, analysis, files, max_entities=3)
    ids = [identifier for _, _, selected in batches for identifier in selected]
    assert len(batches) == 5 and len(ids) == len(set(ids)) == 13
    assert set(ids) == {entity.id for entity in analysis.entities if entity.name.startswith('wrap')}


def test_review_calls_only_selected_functions_and_returns_finite_usage_without_changes():
    architecture, analysis, files, entities = fixture()

    class FixedProvider:
        async def structured(self, system, context, validator, max_tokens):
            from codescape.ledger import request_scope

            assert request_scope.get()['phase'] == 'explanation_review'
            assert max_tokens == 8192
            cards = json.loads(context)['functions']
            assert [card['entity_id'] for card in cards] == [entities['close'].id]
            return validator({'items': [{'entity_id': entities['close'].id, 'decision': 'unchanged',
                                         'reason': '已有依据准确'}]}), {'total_tokens': 12}

    provider = FixedProvider()
    result, usage = asyncio.run(review.review_explanations(provider, architecture, analysis, files))
    assert result == architecture and usage == {'total_tokens': 12}
    assert len(provider._explanation_review_plans) == 1


def test_no_short_forwarders_send_no_request():
    architecture, analysis, files, _ = fixture()
    architecture.explanations = architecture.explanations[:1]

    class FixedProvider:
        async def structured(self, *args, **kwargs):
            pytest.fail('无复核候选时不能发请求')

    result, usage = asyncio.run(review.review_explanations(FixedProvider(), architecture, analysis, files))
    assert result == architecture and usage == {}


def test_explicit_selection_can_review_control_flow_without_adding_unselected_entities():
    architecture, _, _, _ = fixture()
    files = {'a.py': 'def branch(value):\n    if value is None:\n        return 0\n    return value\n'
                    'def other():\n    return 1\n'}
    analysis = python_analysis(files, 'fixed')
    architecture.explanations = [FunctionExplanation(entity_id=entity.id, summary=entity.name, details='固定响应',
        evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})]) for entity in analysis.entities]
    selected = {entity.id for entity in analysis.entities if entity.name == 'branch'}
    _, _, default_ids = review.review_context(architecture, analysis, files)
    assert not default_ids
    context, ranges, ids = review.review_context(architecture, analysis, files, selected_entity_ids=selected)
    cards = json.loads(context)['functions']
    assert ids == selected and len(cards) == 1 and cards[0]['review_kind'] == 'explicit_implementation'
    assert ranges == {'a.py': [(1, 4), (1, 4)]}


def test_selection_does_not_create_notes_for_anonymous_or_unexplained_functions():
    architecture, analysis, files, _ = fixture()
    architecture.explanations = []
    context, _, ids = review.review_context(architecture, analysis, files,
                                          selected_entity_ids={entity.id for entity in analysis.entities})
    assert not ids and json.loads(context)['functions'] == []


@pytest.mark.parametrize('failure', ['provider', 'checkpoint'])
def test_review_preserves_accepted_batch_and_stops_on_next_error(failure):
    from codescape.provider import ProviderError

    architecture, _, _, _ = fixture()
    files = {'a.py': 'def write():\n    return 1\n' + ''.join(
        f'\ndef wrap{number}():\n    return write()\n' for number in range(13))}
    analysis = python_analysis(files, 'fixed')
    architecture.explanations = [FunctionExplanation(entity_id=entity.id, summary=entity.name, details='旧讲解',
        evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})])
        for entity in analysis.entities]
    requests, saved = [], []

    class FixedProvider:
        async def structured(self, system, context, validator, max_tokens):
            requests.append(context)
            if len(requests) > 1:
                raise ProviderError('固定响应服务故障')
            cards = json.loads(context)['functions']
            return validator({'items': [{'entity_id': card['entity_id'], 'decision': 'corrected',
                'reason': '固定响应修订', 'explanation': {**card['existing'], 'details': '已核对的委托'}}
                for card in cards]}), {'total_tokens': 12}

    def persist(result, plans, total):
        if failure == 'checkpoint':
            raise OSError('固定响应磁盘故障，不能回显')
        saved.append((result, plans, total))

    provider = FixedProvider()
    with pytest.raises(ProviderError, match='保存失败' if failure == 'checkpoint' else '服务故障'):
        asyncio.run(review.review_explanations(provider, architecture, analysis, files, on_batch=persist))
    assert all(note.details == '旧讲解' for note in architecture.explanations)
    if failure == 'checkpoint':
        assert len(requests) == 1 and not saved and not provider._explanation_review_plans
        assert provider._explanation_review_architecture == architecture
    else:
        assert len(requests) == 2 and len(saved) == 1
        result, plans, total = saved[0]
        assert len(plans) == 1 and total == 2
        assert provider._explanation_review_plans == plans
        assert provider._explanation_review_architecture == result
        assert sum(note.details == '已核对的委托' for note in result.explanations) == 12
        assert sum(note.details == '旧讲解' for note in result.explanations) == 2
        assert result.modules == architecture.modules and result.call_judgments == architecture.call_judgments


def test_review_resumes_only_validated_prefix_without_repaying_and_rejects_stale_ids():
    architecture, _, _, _ = fixture()
    files = {'a.py': 'def write():\n    return 1\n' + ''.join(
        f'\ndef wrap{number}():\n    return write()\n' for number in range(13))}
    analysis = python_analysis(files, 'fixed')
    architecture.explanations = [FunctionExplanation(entity_id=entity.id, summary=entity.name, details='旧讲解',
        evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})])
        for entity in analysis.entities]
    batches = review.review_batches(architecture, analysis, files)
    first_ids = batches[0][2]
    prefix = {'items': [{'entity_id': note.entity_id, 'decision': 'corrected', 'reason': '固定响应',
        'explanation': {**note.model_dump(), 'details': '检查点讲解'}}
        for note in architecture.explanations if note.entity_id in first_ids]}
    requested, saved = [], []

    class FixedProvider:
        async def structured(self, system, context, validator, max_tokens):
            cards = json.loads(context)['functions']
            requested.extend(card['entity_id'] for card in cards)
            return validator({'items': [{'entity_id': card['entity_id'], 'decision': 'unchanged', 'reason': '准确'}
                                        for card in cards]}), {'total_tokens': 5}

    provider = FixedProvider()
    result, usage = asyncio.run(review.review_explanations(provider, architecture, analysis, files,
        completed_plans=[prefix], on_batch=lambda result, plans, total: saved.append((result, plans, total))))
    assert requested == list(batches[1][2]) and len(requested) == 1
    assert usage == {'total_tokens': 5} and len(saved[0][1]) == saved[0][2] == 2
    assert sum(note.details == '检查点讲解' for note in result.explanations) == 12
    assert all(note.details == '旧讲解' for note in architecture.explanations)
    requested.clear()
    cached, usage = asyncio.run(review.review_explanations(provider, architecture, analysis, files,
                                                          completed_plans=saved[0][1]))
    assert not requested and cached == result and usage == {}
    prefix['items'][0]['entity_id'] = '旧快照ID'
    prefix['items'][0]['explanation']['entity_id'] = '旧快照ID'
    with pytest.raises(ValueError, match='恰好出现一次'):
        asyncio.run(review.review_explanations(provider, architecture, analysis, files, completed_plans=[prefix]))
    assert not requested
