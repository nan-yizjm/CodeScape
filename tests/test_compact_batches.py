from codescape.analyzers import python_analysis
from codescape.models import Architecture, FunctionExplanation, Module, Overview
from codescape.provider import Provider


def fixtures():
    files = {'owned.py': ''.join(f'def f{index}():\n    return {index}\n\n' for index in range(20))}
    return files, python_analysis(files, 'fixed')


def accepted(provider, analysis, batch):
    source, ranges = batch
    _, expected = provider._context(analysis, source, ranges)
    return Architecture(modules=[Module(id='m', name='固定样例', summary='返回常量',
        entity_ids=sorted(expected), code_paths=list(source))],
        overview=Overview(introduction='自有样例', principle='返回', capabilities=['常量'], limitations=[]),
        explanations=[FunctionExplanation(entity_id=entity.id, summary='常量', details='固定响应',
            evidence=[entity.location.model_dump(include={'path', 'start_line', 'end_line'})])
            for entity in analysis.entities if entity.id in expected])


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
    cached = accepted(provider, analysis, plan[0])
    assert provider._align_completed_batches([cached], plan, analysis, set(files), files=files) == []


def test_source_only_checkpoint_can_identify_unique_unit_with_unlisted_config_file(tmp_path):
    files = {'owned.py': 'VALUE = 1\n', 'package.json': '{"name":"owned"}\n'}
    analysis = python_analysis(files, 'fixed')
    provider = Provider(tmp_path)
    plan = [(files, {'owned.py': (1, 1), 'package.json': (1, 1)})]
    cached = accepted(provider, analysis, plan[0])
    cached.modules[0].code_paths = ['owned.py']
    assert provider._align_completed_batches([cached], plan, analysis, set(files), files=files) == [cached]
