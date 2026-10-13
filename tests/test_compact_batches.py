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
