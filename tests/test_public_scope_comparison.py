import hashlib
import json
from copy import deepcopy

import pytest
from codescape.provider import PIPELINE_VERSION, PREVIOUS_SOURCE_PIPELINE_VERSION, SOURCE_PIPELINE_VERSION

from scripts.compare_public_scope import (
    acceptance_gaps,
    align_entities,
    compare,
    confirmed_calls,
    entity_key,
    explanation,
    index_receipt,
    module_lineage,
)


def entity(identifier='full', **values):
    return {'id': identifier, 'visible': True, 'qualified_name': 'work',
            'implementation_hash': 'exact-body', 'condition': None,
            'location': {'path': 'a.py', 'start_line': 1, 'end_line': 2,
                         'start_byte': 0, 'end_byte': 30}, **values}


def architecture(identifier):
    return {'modules': [{'id': 'leaf', 'parent_id': None, 'name': '工作处理', 'summary': '按输入执行步骤',
                         'incomplete': False, 'entity_ids': [identifier]}],
            'explanations': [{'entity_id': identifier, 'summary': '处理', 'details': '返回结果', 'evidence': [
                {'path': 'a.py', 'start_line': 1, 'end_line': 2}]}],
            'overview': {'introduction': '自有测试', 'capabilities': ['处理'], 'principle': '执行步骤',
                         'limitations': ['未运行']}}


def scope(identifier, *, published=True, pipeline=PIPELINE_VERSION):
    return {'record': {'流水线': pipeline, '模型': '固定响应', '接收端': '本地', '评测期间实施代码一致': True,
                       '源码生成版本集合': [pipeline.removesuffix('-organization/1')]},
            'published': published, 'architecture': architecture(identifier) if published else None,
            'batches': [] if published else [architecture(identifier)],
            'analysis': {'entities': [entity(identifier)], 'calls': [], 'scopes': []},
            'file_hashes': {'a.py': 'same-bytes'}, 'proof': {'报告sha256': 'fixed'}}


def test_cross_snapshot_ids_match_by_exact_source_not_name():
    full, narrow = entity(), entity('narrow')
    assert align_entities([full], [narrow])[0] == [(full, narrow)]
    for other in (entity('other', implementation_hash='different-body'),
                  entity('other', condition='defined(OTHER)'),
                  entity('other', location={**full['location'], 'start_byte': 100, 'end_byte': 130})):
        assert align_entities([full], [other])[0] == []


def test_duplicate_identity_is_ambiguous_not_arbitrarily_matched():
    matches, ambiguous = align_entities([entity(), entity('duplicate')], [entity('narrow')])
    assert not matches and ambiguous == [entity_key(entity())]
    assert align_entities([entity(visible=False)], [entity('narrow')]) == ([], [])


def test_partial_checkpoint_never_supplies_global_overview_or_passes_acceptance():
    full, narrow = scope('full', published=False), scope('narrow')
    report = compare(full, narrow)
    assert report['共同可见函数数'] == report['共同函数在完整范围有唯一讲解数'] == 1
    assert report['完整范围全局概览'] is None and report['完整范围检查点批数'] == 1
    assert not report['验收通过'] and any('检查点不作为全局概览' in gap for gap in report['尚缺'])


def test_identical_machine_results_still_require_semantic_review():
    report = compare(scope('full'), scope('narrow'))
    assert report['完整范围结果已发布'] and report['最终流水线一致']
    assert not report['验收通过'] and len(report['尚缺']) == 1


def test_one_unavailable_sample_does_not_suppress_other_scope_comparisons(monkeypatch, tmp_path):
    import scripts.compare_public_scope as comparator

    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/benchmark_sources.json').write_text(json.dumps({'repositories': [
        {'id': 'hiredis-c', 'commit': 'fixed'}, {'id': 'flask-python', 'commit': 'fixed'},
    ]}), encoding='utf-8')
    monkeypatch.setattr(comparator, 'REPOSITORY', tmp_path)
    monkeypatch.setattr(comparator, 'REPORTS', tmp_path)

    def load(sample, scope_name, commit):
        if sample == 'hiredis-c':
            raise RuntimeError('缺少同一源码阶段的静态索引凭据')
        return scope(scope_name)

    monkeypatch.setattr(comparator, 'load_scope', load)
    comparator.main()
    report = json.loads((tmp_path / '全窄范围源码与结果对照.json').read_text(encoding='utf-8'))
    pending = report['样本']['hiredis-c']
    assert not pending['验收通过'] and pending['尚缺'] == ['缺少同一源码阶段的静态索引凭据']
    assert '完整范围全局概览' not in pending
    assert report['样本']['flask-python']['共同可见函数数'] == 1
    assert report['模型请求'] == 0


def test_same_processing_version_does_not_hide_reused_historical_generation():
    full, narrow = scope('full'), scope('narrow')
    full['source_generation_versions'] = [PREVIOUS_SOURCE_PIPELINE_VERSION, SOURCE_PIPELINE_VERSION]
    report = compare(full, narrow)
    assert report['最终流水线一致'] and not report['源码生成阶段一致']
    assert any('实际源码生成来源混合' in gap for gap in report['尚缺'])
    for values in ([], ['unknown'], None):
        full.pop('source_generation_versions')
        full['record']['源码生成版本集合'] = values
        assert not compare(full, narrow)['源码生成阶段一致']
        full['source_generation_versions'] = values


def test_source_checkpoint_compatibility_does_not_imply_final_pipeline_equivalence():
    full, narrow = scope('full'), scope('narrow', pipeline=SOURCE_PIPELINE_VERSION)
    report = compare(full, narrow)
    assert report['源码生成阶段一致'] and not report['最终流水线一致']
    assert any('最终流水线不同' in gap for gap in acceptance_gaps(full, narrow))
    narrow['record']['模型'] = 'different'
    assert '接收端或模型不同' in acceptance_gaps(full, narrow)


def test_common_file_bytes_must_match_before_comparison():
    full, narrow = scope('full'), scope('narrow')
    narrow['file_hashes']['a.py'] = 'changed'
    with pytest.raises(AssertionError, match='共同文件字节不同'):
        compare(full, narrow)


def test_missing_terminal_code_identity_and_non_subset_scope_remain_gaps():
    full, narrow = scope('full'), scope('narrow')
    full['record']['评测期间实施代码一致'] = None
    narrow['file_hashes']['excluded.py'] = 'other'
    report = compare(full, narrow)
    assert '完整范围缺少评测全程实施代码一致的终态证明' in report['尚缺']
    assert '缩小范围存在不属于完整范围的文件' in report['尚缺']


def test_missing_and_repeated_explanations_are_not_counted_as_unique():
    item = architecture('full')
    assert explanation([], 'full')['讲解数量'] == 0
    assert explanation([item, item], 'full')['讲解数量'] == 2
    assert explanation([item], 'full')['状态'] == '有讲解'
    item['modules'].append({**item['modules'][0], 'id': 'duplicate'})
    assert module_lineage(item, 'full')['归属数量'] == 2


def test_anonymous_scope_is_not_promoted_and_ai_judgment_not_counted_as_static():
    location = entity()['location']
    analysis = {'scopes': [{'id': 'anon', 'kind': 'anonymous', 'name': '<lambda>', 'location': location}],
                'calls': [{'caller_scope_id': 'anon', 'target_id': 'full', 'resolution': 'confirmed',
                           'location': location, 'expression': 'work()', 'kind': 'direct', 'origin': 'static'}]}
    ids = {'full': entity_key(entity())}
    anonymous = confirmed_calls(analysis, ids, {'a.py'})
    assert len(anonymous) == 1 and next(iter(anonymous))[0] == 'anonymous'
    ordinary = deepcopy(analysis)
    ordinary['scopes'][0]['kind'] = 'function'
    ordinary['scopes'][0]['name'] = 'outer'
    assert anonymous.isdisjoint(confirmed_calls(ordinary, ids, {'a.py'}))
    analysis['calls'][0]['origin'] = 'ai_source_judgment'
    assert not confirmed_calls(analysis, ids, {'a.py'})


def test_invalid_parent_or_cycle_is_explicit_in_module_lineage():
    item = architecture('full')
    item['modules'][0]['parent_id'] = 'missing'
    assert module_lineage(item, 'full')['状态'] == '父模块缺失'
    item['modules'][0]['parent_id'] = 'leaf'
    assert module_lineage(item, 'full')['状态'] == '层级有环'


def test_running_report_only_reuses_exact_snapshot_archive_index(monkeypatch, tmp_path):
    import scripts.compare_public_scope as comparator

    monkeypatch.setattr(comparator, 'REPORTS', tmp_path)
    current = tmp_path / 'fixture-full真实模型验证.json'
    record = {'Commit': 'fixed', '数据目录': 'isolated', 'project_id': 'p', 'version_id': 'v',
              '实际快照hash': 'snapshot', '接收端': 'local', '模型': 'fixture', '实际文件': ['a.py'],
              '流水线': PIPELINE_VERSION}
    current.write_text(json.dumps(record), encoding='utf-8')
    prior = {**record, '流水线': SOURCE_PIPELINE_VERSION, '静态索引制品': {'sha256': 'verified-index'}}
    archive = tmp_path / 'fixture-full真实模型验证-prior.json'
    archive.write_text(json.dumps(prior), encoding='utf-8')
    artifact, source, checksum = index_receipt(record, current)
    assert artifact == prior['静态索引制品'] and source == archive
    assert checksum == hashlib.sha256(archive.read_bytes()).hexdigest()
    for key in ('实际快照hash', '模型', 'version_id', 'Commit', '实际文件', '流水线'):
        changed = {**record, key: 'different'}
        with pytest.raises(RuntimeError, match='同快照静态索引'):
            index_receipt(changed, current)
