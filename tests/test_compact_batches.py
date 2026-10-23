import asyncio
import json

import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.provider import Provider, ProviderError


def fixtures():
    files = {'owned.py': ''.join(f'def f{index}():\n    return {index}\n\n' for index in range(20))}
    return files, python_analysis(files, 'fixed')


def accepted(provider, analysis, batch, *, tracked=True):
    source, ranges = batch
    _, expected = provider._context(analysis, source, ranges)
    result = Architecture(modules=[Module(id='m', name='固定样例', summary='返回常量',
        entity_ids=sorted(expected), code_paths=list(source))],
        overview=Overview(introduction='自有样例', principle='返回', capabilities=['常量'], limitations=[]),
        explanations=[FunctionExplanation(entity_id=entity.id, summary='常量', details='固定响应',
            evidence=[{'path': entity.location.path,
                       'start_line': max(entity.location.start_line, ranges[entity.location.path][0]),
                       'end_line': min(entity.location.end_line, ranges[entity.location.path][1])}])
            for entity in analysis.entities if entity.id in expected])
    if tracked:
        result._source_ranges = dict(ranges)
    return result


def test_compaction_preserves_all_bytes_lines_entities_and_whole_function_boundaries(tmp_path):
    files, analysis = fixtures()
    provider = Provider(tmp_path)
    base = provider._plan_batches(analysis, files)
    batches, restored = provider._execution_batches(analysis, files, [])
    assert not restored and len(batches) > len(base)
    assert ''.join(batch[0]['owned.py'] for batch in batches) == files['owned.py']
    ids = []
    for source, ranges in batches:
        _, expected = provider._context(analysis, source, ranges)
        assert len(expected) <= 4
        ids.extend(expected)
        for entity in analysis.entities:
            if entity.id in expected:
                assert ranges['owned.py'][0] <= entity.location.start_line <= entity.location.end_line <= ranges['owned.py'][1]
    assert len(ids) == len(set(ids)) == 20


def test_resume_keeps_large_completed_units_and_partial_small_units_without_merging_or_repaying(tmp_path):
    files, analysis = fixtures()
    provider = Provider(tmp_path)
    original = provider._plan_batches(analysis, files)
    large = accepted(provider, analysis, original[0])
    plan, restored = provider._execution_batches(analysis, files, [large])
    assert restored == [large] and plan[0] == original[0]
    small = accepted(provider, analysis, plan[1])
    next_plan, next_restored = provider._execution_batches(analysis, files, [large, small])
    assert next_plan == plan and next_restored == [large, small]
    all_saved = [large, *(accepted(provider, analysis, batch) for batch in plan[1:])]
    final_plan, final_restored = provider._execution_batches(analysis, files, all_saved)
    assert final_plan == plan and final_restored == all_saved


def test_resume_preserves_source_only_units_before_and_after_functions(tmp_path):
    files, _ = fixtures()
    files = {'a.py': '"""可读说明，无函数。"""\n', **files, 'z.py': 'VALUE = 1\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    plan, _ = provider._execution_batches(analysis, files, [])
    saved = [accepted(provider, analysis, batch) for batch in plan]
    assert any(not module.entity_ids for result in saved for module in result.modules)
    restored_plan, restored = provider._execution_batches(analysis, files, saved)
    assert restored_plan == plan and restored == saved


def test_source_only_checkpoint_does_not_match_ambiguous_ranges(tmp_path):
    files = {'owned.py': 'VALUE = 1\nOTHER = 2\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    plan = [({'owned.py': 'VALUE = 1\n'}, {'owned.py': (1, 1)}),
            ({'owned.py': 'OTHER = 2\n'}, {'owned.py': (2, 2)})]
    cached = accepted(provider, analysis, plan[0], tracked=False)
    assert provider._align_completed_batches([cached], plan, analysis, set(files), files=files) == []


def test_source_only_checkpoint_can_identify_unique_unit_with_unlisted_config_file(tmp_path):
    files = {'owned.py': 'VALUE = 1\n', 'package.json': '{"name":"owned"}\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    plan = [(files, {'owned.py': (1, 1), 'package.json': (1, 1)})]
    cached = accepted(provider, analysis, plan[0])
    cached.modules[0].code_paths = ['owned.py']
    assert provider._align_completed_batches([cached], plan, analysis, set(files), files=files) == [cached]


def test_resume_after_truncation_keeps_first_subbatch_without_sending_it_again(tmp_path, monkeypatch):
    files = {'owned.py': ''.join(f'def f{index}():\n    return {index}\n\n' for index in range(4))}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    sent, saved = [], []

    async def read_batch(system, context, validator, current_analysis, current_files, ranges):
        payload = json.loads(context)
        sent.append({entity['id'] for entity in payload['entities']})
        if len(sent) == 1:
            raise ProviderError('模型输出被截断，请缩小分析批次。')
        if len(sent) == 3:
            raise ProviderError('人工暂停样例，无模型请求。')
        return accepted(provider, current_analysis, (payload['files'], ranges)), {}

    async def save_batch(completed, total, architecture):
        saved.append(architecture)

    async def organize(architecture, current_analysis):
        return architecture, {}

    async def overview(architecture, current_analysis):
        return architecture.overview.model_copy(update={'synthesized': True}), {}

    monkeypatch.setattr(provider, '_read_batch', read_batch)
    monkeypatch.setattr(provider, 'organize_modules', organize)
    monkeypatch.setattr(provider, 'project_overview', overview)
    with pytest.raises(ProviderError, match='人工暂停'):
        asyncio.run(provider.architecture(analysis, files, on_batch=save_batch))
    assert len(saved) == 1 and len(sent[1]) == 2

    result, _ = asyncio.run(provider.architecture(analysis, files, completed_batches=saved))
    assert len(sent) == 4 and sent[3] == sent[2]
    assert sent[3].isdisjoint(sent[1])
    assert {identifier for module in result.modules for identifier in module.entity_ids} == sent[0]


def test_partial_dynamic_subbatch_preserves_prior_completed_units_and_all_source_bytes(tmp_path):
    files = {path: ''.join(f'def {prefix}{index}():\n    return {index}\n\n' for index in range(4))
             for path, prefix in [('a.py', 'a'), ('b.py', 'b')]}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    assert len(original) == 2
    first = accepted(provider, analysis, original[0])
    split = provider._split_batch(*original[1])
    child = accepted(provider, analysis, split[0])
    plan, restored = provider._execution_batches(analysis, files, [first, child])
    assert plan == [original[0], *split] and restored == [first, child]
    for path, source in files.items():
        assert ''.join(batch_files.get(path, '') for batch_files, _ in plan) == source


def test_complete_dynamic_subbatches_still_recombine_without_model_requests(tmp_path):
    files = {'owned.py': ''.join(f'def f{index}():\n    return {index}\n\n' for index in range(4))}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = [accepted(provider, analysis, batch) for batch in provider._split_batch(*original[0])]
    plan, restored = provider._execution_batches(analysis, files, saved)
    assert plan == original and len(restored) == 1
    assert {identifier for module in restored[0].modules for identifier in module.entity_ids} == {
        entity.id for entity in analysis.entities}


def test_source_only_partial_file_group_does_not_claim_unread_files_complete(tmp_path):
    files = {'a.py': 'VALUE = 1\n', 'b.py': 'OTHER = 2\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    assert len(original) == 1
    split = provider._split_batch(*original[0])
    saved = accepted(provider, analysis, split[0])
    plan, restored = provider._execution_batches(analysis, files, [saved])
    assert plan == split and restored == [saved]
    assert plan[len(restored)][0] == {'b.py': files['b.py']}


def test_partial_overlapping_function_fragments_are_not_guessed_on_resume(tmp_path):
    files = {'owned.py': 'def long_function():\n' + '    value = 1\n' * 10
             + '    return value\n\ndef other_function(): return 2\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    split = provider._split_batch(*original[0])
    left = provider._context(analysis, *split[0])[1]
    right = provider._context(analysis, *split[1])[1]
    assert left & right and left != right
    saved = accepted(provider, analysis, split[0], tracked=False)
    saved.explanations = []
    plan, restored = provider._execution_batches(analysis, files, [saved])
    assert plan == original and restored == []


@pytest.mark.parametrize('invalid', ['unknown_entity', 'invalid_evidence'])
def test_dynamic_subbatch_resume_keeps_strict_checkpoint_validation(tmp_path, invalid):
    files = {'owned.py': ''.join(f'def f{index}():\n    return {index}\n\n' for index in range(4))}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, provider._split_batch(*original[0])[0])
    if invalid == 'unknown_entity':
        saved.modules[0].entity_ids[0] = 'unknown-entity'
    else:
        saved.explanations[0].evidence[0].end_line = 999
    with pytest.raises(ProviderError, match='检查点.*(?:未通过|不一致)'):
        provider._execution_batches(analysis, files, [saved])


@pytest.mark.parametrize('files', [
    {'owned.py': 'def only_function():\n' + '    value = 1\n' * 10 + '    return value\n'},
    {'owned.py': 'VALUE = 1\nOTHER = 2\nTHIRD = 3\nLAST = 4\n'},
    {'package.json': '{\n  "name": "owned",\n  "private": true\n}\n'},
])
def test_actual_ranges_resume_same_entity_or_entity_free_fragments_without_skipping_tail(tmp_path, files):
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    split = provider._split_batch(*original[0])
    saved = accepted(provider, analysis, split[0])
    assert provider._context(analysis, *split[0])[1] == provider._context(analysis, *split[1])[1]
    plan, restored = provider._execution_batches(analysis, files, [saved])
    assert plan == split and restored == [saved]
    assert plan[len(restored)] == split[1]
    completed = [saved, accepted(provider, analysis, split[1])]
    full_plan, full_restored = provider._execution_batches(analysis, files, completed)
    assert full_plan == original and len(full_restored) == 1
    assert full_restored[0]._source_ranges == original[0][1]


@pytest.mark.parametrize('files', [
    {'owned.py': 'def only_function():\n' + '    value = 1\n' * 10 + '    return value\n'},
    {'owned.py': 'VALUE = 1\nOTHER = 2\nTHIRD = 3\nLAST = 4\n'},
    {'package.json': '{\n  "name": "owned",\n  "private": true\n}\n'},
])
def test_legacy_same_signature_partial_fragments_never_claim_whole_batch_complete(tmp_path, files):
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, provider._split_batch(*original[0])[0], tracked=False)
    plan, restored = provider._execution_batches(analysis, files, [saved])
    assert plan == original and restored == []


def test_range_checkpoint_covers_config_files_even_when_modules_do_not_list_them(tmp_path):
    files = {'owned.py': 'def only_function(): return 1\n', 'package.json': '{"name":"owned"}\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    partial = accepted(provider, analysis, provider._split_batch(*original[0])[0])
    plan, restored = provider._execution_batches(analysis, files, [partial])
    assert len(restored) == 1 and plan[1][0] == {'package.json': files['package.json']}
    whole = accepted(provider, analysis, original[0])
    whole.modules[0].code_paths = ['owned.py']
    plan, restored = provider._execution_batches(analysis, files, [whole])
    assert plan == original and restored == [whole]
    whole._source_ranges = None
    plan, restored = provider._execution_batches(analysis, files, [whole])
    assert len(restored) < len(plan)
    assert 'package.json' in plan[len(restored)][0]


def test_deep_saved_prefix_rebuilds_actual_cut_and_never_claims_a_gap_complete(tmp_path):
    files = {'owned.py': 'VALUE = 1\n' * 8}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    halves = provider._split_batch(*original[0])
    quarters = provider._split_batch(*halves[0])
    first = accepted(provider, analysis, quarters[0])
    later = accepted(provider, analysis, halves[1])
    plan, restored = provider._execution_batches(analysis, files, [first, later])
    assert restored == [first] and plan[0] == quarters[0]
    assert plan[1][1] == {'owned.py': (3, 8)}
    assert ''.join(source['owned.py'] for source, _ in plan) == files['owned.py']


@pytest.mark.parametrize('ranges', [
    {}, {'owned.py': (0, 1)}, {'owned.py': (2, 1)}, {'owned.py': (1, 999)},
    {'owned.py': (True, 1)}, {'unknown.py': (1, 1)}, {'owned.py': (1,)},
])
def test_invalid_actual_ranges_stop_before_replanning(tmp_path, ranges):
    files = {'owned.py': 'VALUE = 1\nOTHER = 2\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, original[0])
    saved._source_ranges = ranges
    with pytest.raises(ProviderError, match='实际范围无效'):
        provider._execution_batches(analysis, files, [saved])


def test_private_source_ranges_are_not_public_architecture_fields(tmp_path):
    files = {'owned.py': 'VALUE = 1\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    plan, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, plan[0])
    assert saved._source_ranges == {'owned.py': (1, 1)}
    assert '_source_ranges' not in saved.model_dump()
    assert '_source_ranges' not in Architecture.model_json_schema()['properties']


def test_complete_range_checkpoint_takes_precedence_over_ambiguous_legacy_fragment(tmp_path):
    files = {'owned.py': 'VALUE = 1\nOTHER = 2\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    legacy = accepted(provider, analysis, provider._split_batch(*original[0])[0], tracked=False)
    complete = accepted(provider, analysis, original[0])
    plan, restored = provider._execution_batches(analysis, files, [legacy, complete])
    assert plan == original and restored == [complete]


def test_range_checkpoint_cannot_relabel_another_files_module_as_completed(tmp_path):
    files = {'owned.py': 'VALUE = 1\n', 'package.json': '{"name":"owned"}\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, provider._split_batch(*original[0])[0])
    saved._source_ranges = {'package.json': (1, 1)}
    with pytest.raises(ProviderError, match='旧源码检查点未通过'):
        provider._execution_batches(analysis, files, [saved])


def test_legacy_fragments_covering_all_entities_cannot_skip_unrecorded_tail_lines(tmp_path):
    lines = ['VALUE = 1\n'] * 100
    lines[0] = 'def first(): return 1\n'
    lines[69] = 'def second(): return 2\n'
    files = {'owned.py': ''.join(lines)}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = [accepted(provider, analysis, ({'owned.py': ''.join(lines[start - 1:end])},
                                           {'owned.py': (start, end)}), tracked=False)
             for start, end in [(1, 50), (51, 75)]]
    assert {identifier for result in saved for module in result.modules for identifier in module.entity_ids} == {
        entity.id for entity in analysis.entities}
    plan, restored = provider._execution_batches(analysis, files, saved)
    assert restored == [] and plan == original
    assert ''.join(source['owned.py'] for source, _ in plan) == files['owned.py']


@pytest.mark.parametrize('files', [{'owned.py': 'def only_function(): return 1\n'},
                                  {'package.json': '{"name":"owned"}\n'}])
def test_legacy_single_file_single_line_units_remain_reusable(tmp_path, files):
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    original, _ = provider._execution_batches(analysis, files, [])
    saved = accepted(provider, analysis, original[0], tracked=False)
    plan, restored = provider._execution_batches(analysis, files, [saved])
    assert plan == original and restored == [saved]
