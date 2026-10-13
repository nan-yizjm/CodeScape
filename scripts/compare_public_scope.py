"""只读对照固定公开样本的全/窄范围；不调用模型、不执行目标代码、不自动判定语义质量。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from codescape.models import Analysis
from codescape.provider import KNOWN_SOURCE_PIPELINE_VERSIONS, SOURCE_PIPELINE_VERSION

REPOSITORY = Path(__file__).resolve().parents[1]
REPORTS = REPOSITORY / 'docs/reports/p0'


def entity_key(entity):
    location = entity['location']
    return (location['path'], location['start_byte'], location['end_byte'],
            entity['implementation_hash'], entity.get('condition'))


def align_entities(full, narrow):
    """跨快照ID不可靠；同名、条件分支、局部作用域和歧义均不能代替源码身份。"""
    indexes = []
    for entities in (full, narrow):
        index = defaultdict(list)
        for entity in entities:
            if entity['visible']:
                index[entity_key(entity)].append(entity)
        indexes.append(index)
    full_index, narrow_index = indexes
    common = full_index.keys() & narrow_index.keys()
    matches = [(full_index[key][0], narrow_index[key][0]) for key in sorted(common, key=str)
               if len(full_index[key]) == len(narrow_index[key]) == 1]
    ambiguous = [key for key in common if len(full_index[key]) != 1 or len(narrow_index[key]) != 1]
    return matches, sorted(ambiguous, key=str)


def module_lineage(architecture, entity_id):
    modules = {module['id']: module for module in architecture.get('modules', [])}
    owners = [module for module in modules.values() if entity_id in module['entity_ids']]
    if len(owners) != 1:
        return {'状态': '缺少唯一归属', '归属数量': len(owners)}
    module, seen, lineage = owners[0], set(), []
    while module:
        if module['id'] in seen:
            return {'状态': '层级有环'}
        seen.add(module['id'])
        lineage.append({'名称': module['name'], '职责': module['summary'], '未完成': module['incomplete']})
        parent = module.get('parent_id')
        if parent and parent not in modules:
            return {'状态': '父模块缺失'}
        module = modules.get(parent)
    return {'状态': '归属唯一', '层级': list(reversed(lineage))}


def explanation(architectures, entity_id):
    matches = [note for architecture in architectures for note in architecture.get('explanations', [])
               if note['entity_id'] == entity_id]
    if len(matches) != 1:
        return {'状态': '未找到唯一讲解', '讲解数量': len(matches)}
    note = matches[0]
    return {'状态': '有讲解', '摘要': note['summary'], '详情': note['details'], '依据': note['evidence']}


def source_stage(pipeline):
    if isinstance(pipeline, str):
        source = pipeline.removesuffix('-organization/1')
        if source in KNOWN_SOURCE_PIPELINE_VERSIONS:
            return source
    return pipeline


def index_receipt(record, path):
    """恢复中报告尚未回填索引时，只读同一快照/版本/源码阶段的归档凭据。"""
    if record.get('静态索引制品'):
        return record['静态索引制品'], path, hashlib.sha256(path.read_bytes()).hexdigest()
    for candidate in sorted(REPORTS.glob(path.stem + '-*.json'), key=lambda value: value.stat().st_mtime,
                            reverse=True):
        raw = candidate.read_bytes()
        prior = json.loads(raw)
        identity = ('Commit', '数据目录', 'project_id', 'version_id', '实际快照hash', '接收端', '模型', '实际文件')
        if (all(record.get(key) == prior.get(key) for key in identity)
                and source_stage(record['流水线']) == source_stage(prior['流水线'])
                and prior.get('静态索引制品')):
            return prior['静态索引制品'], candidate, hashlib.sha256(raw).hexdigest()
    raise RuntimeError('当前评测还没有可核对的同快照静态索引凭据；等待终态后再生成范围对照。')


def acceptance_gaps(full, narrow):
    gaps = []
    for label, item in (('完整范围', full), ('缩小范围', narrow)):
        if not item['published']:
            gaps.append(label + '尚未发布完整架构，检查点不作为全局概览')
        if item['record'].get('评测期间实施代码一致') is not True:
            gaps.append(label + '缺少评测全程实施代码一致的终态证明')
    if full['record']['流水线'] != narrow['record']['流水线']:
        gaps.append('最终流水线不同；相同源码生成阶段不能替代最终组织与概览对照')
    if full['record']['模型'] != narrow['record']['模型'] or full['record']['接收端'] != narrow['record']['接收端']:
        gaps.append('接收端或模型不同')
    if source_stage(full['record']['流水线']) != source_stage(narrow['record']['流水线']):
        gaps.append('源码生成阶段不同')
    a, b = generation_versions(full), generation_versions(narrow)
    if not a or not b or len(a) != 1 or len(b) != 1 or a != b:
        gaps.append('实际源码生成来源混合、不同或缺失，不能按当前处理版本视为同一生成阶段')
    return gaps


def generation_versions(scope):
    values = scope.get('source_generation_versions')
    if values is None:
        values = scope['record'].get('源码生成版本集合')
    if values is None:
        source = source_stage(scope['record']['流水线'])
        if source == SOURCE_PIPELINE_VERSION:
            return frozenset()
        return frozenset([source]) if source else frozenset()
    if (not isinstance(values, list) or not values
            or any(not isinstance(value, str) or value not in KNOWN_SOURCE_PIPELINE_VERSIONS for value in values)):
        return frozenset()
    return frozenset(values)


def confirmed_calls(analysis, common_ids, common_paths):
    """只对照静态确认且两端可匹配的调用；保留实际调用作用域，不提升匿名调用。"""
    scopes = {scope['id']: scope for scope in analysis['scopes']}
    result = set()
    for call in analysis['calls']:
        scope = scopes.get(call['caller_scope_id'])
        target = common_ids.get(call.get('target_id'))
        if (call['resolution'] != 'confirmed' or call.get('origin', 'static') != 'static'
                or not scope or target is None):
            continue
        location = call['location']
        owner = scope['location']
        if owner['path'] not in common_paths:
            continue
        result.add((scope['kind'], scope['name'], owner['path'], owner['start_byte'], owner['end_byte'],
                    location['path'], location['start_byte'], location['end_byte'], call['expression'],
                    call.get('kind', 'direct'), target))
    return result


def load_scope(sample, scope, commit):
    path = REPORTS / f'{sample}-{scope}真实模型验证.json'
    raw = path.read_bytes()
    record = json.loads(raw)
    assert record['Commit'] == commit and record['执行目标代码'] is False
    root = Path(record['数据目录']).resolve(strict=True)
    root.relative_to(REPOSITORY / '.runtime/public-model-validation')
    artifact, index_receipt_path, index_receipt_hash = index_receipt(record, path)
    index_path = Path(artifact['path']).resolve(strict=True)
    index_path.relative_to(root / 'validation-artifacts')
    index_raw = index_path.read_bytes()
    assert hashlib.sha256(index_raw).hexdigest() == artifact['sha256']
    analysis = Analysis.model_validate_json(index_raw).model_dump()
    source_root = (root / 'owned-source').resolve(strict=True)
    hashes = {}
    for name in record['实际文件']:
        source_path = (source_root / name).resolve(strict=True)
        source_path.relative_to(source_root)
        hashes[name] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        project = connection.execute('SELECT status,version_id FROM prototype_projects WHERE id=?',
                                     (record['project_id'],)).fetchone()
    published = (project is not None and project['status'] == 'ready'
                 and project['version_id'] == record['version_id']
                 and record['状态'] == '结构检查通过，待源码质量复核' and bool(record.get('architecture')))
    architecture = record['architecture'] if published else None
    checkpoint_path = root / 'projects' / record['project_id'] / 'architecture-checkpoint.json'
    checkpoint, checkpoint_hash = None, None
    if not published and checkpoint_path.exists():
        checkpoint_raw = checkpoint_path.read_bytes()
        checkpoint = json.loads(checkpoint_raw)
        assert checkpoint['version_id'] == record['version_id']
        assert checkpoint['snapshot_hash'] == record['实际快照hash']
        assert checkpoint['provider_config'] == {'base_url': record['接收端'], 'model': record['模型']}
        checkpoint_hash = hashlib.sha256(checkpoint_raw).hexdigest()
    return {'record': record, 'analysis': analysis, 'published': published, 'architecture': architecture,
            'source_generation_versions': (checkpoint or {}).get('source_generation_versions'),
            'batches': (checkpoint or {}).get('batches', []), 'file_hashes': hashes,
            'proof': {'报告': str(path), '报告sha256': hashlib.sha256(raw).hexdigest(),
                      '索引来源报告': str(index_receipt_path), '索引来源报告sha256': index_receipt_hash,
                      '索引sha256': artifact['sha256'], '检查点sha256': checkpoint_hash,
                      '检查点流水线': (checkpoint or {}).get('pipeline_version'),
                      '数据库状态': project['status'] if project else None}}


def compare(full, narrow):
    matches, ambiguous = align_entities(full['analysis']['entities'], narrow['analysis']['entities'])
    same_files = full['file_hashes'].keys() & narrow['file_hashes'].keys()
    changed = [name for name in sorted(same_files) if full['file_hashes'][name] != narrow['file_hashes'][name]]
    assert not changed, '共同文件字节不同，不能作为同Commit范围对照'
    full_architectures = [full['architecture']] if full['published'] else full['batches']
    narrow_architectures = [narrow['architecture']] if narrow['published'] else narrow['batches']
    rows = []
    for a, b in matches:
        rows.append({'名称': a['qualified_name'], '位置': a['location'], '条件': a.get('condition'),
                     '实现sha256': a['implementation_hash'], '索引名称一致': a['qualified_name'] == b['qualified_name'],
                     '完整范围讲解': explanation(full_architectures, a['id']),
                     '缩小范围讲解': explanation(narrow_architectures, b['id']),
                     '完整范围归属': [module_lineage(item, a['id']) for item in full_architectures
                                    if any(a['id'] in m['entity_ids'] for m in item['modules'])],
                     '缩小范围归属': [module_lineage(item, b['id']) for item in narrow_architectures
                                    if any(b['id'] in m['entity_ids'] for m in item['modules'])]})
    full_calls = confirmed_calls(full['analysis'], {a['id']: entity_key(a) for a, _ in matches}, same_files)
    narrow_calls = confirmed_calls(narrow['analysis'], {b['id']: entity_key(b) for _, b in matches}, same_files)
    gaps = acceptance_gaps(full, narrow)
    if narrow['file_hashes'].keys() - full['file_hashes'].keys():
        gaps.append('缩小范围存在不属于完整范围的文件')
    if ambiguous:
        gaps.append('存在共同函数身份歧义，未猜测配对')
    return {'完整范围来源': full['proof'], '缩小范围来源': narrow['proof'],
            '完整范围流水线': full['record']['流水线'], '缩小范围流水线': narrow['record']['流水线'],
            '完整范围源码生成来源': sorted(generation_versions(full)),
            '缩小范围源码生成来源': sorted(generation_versions(narrow)),
            '源码生成阶段一致': (len(generation_versions(full)) == len(generation_versions(narrow)) == 1
                                and generation_versions(full) == generation_versions(narrow)),
            '最终流水线一致': full['record']['流水线'] == narrow['record']['流水线'],
            '共同文件数': len(same_files), '共同文件字节一致': True,
            '仅完整范围文件数': len(full['file_hashes'].keys() - narrow['file_hashes'].keys()),
            '共同可见函数数': len(matches), '身份歧义数': len(ambiguous),
            '仅完整范围可见函数数': sum(e['visible'] for e in full['analysis']['entities']) - len(matches),
            '窄范围未匹配函数': [e['qualified_name'] for e in narrow['analysis']['entities']
                              if e['visible'] and e['id'] not in {b['id'] for _, b in matches}],
            '完整范围结果已发布': full['published'], '缩小范围结果已发布': narrow['published'],
            '完整范围检查点批数': len(full['batches']),
            '共同函数在完整范围有唯一讲解数': sum(row['完整范围讲解']['状态'] == '有讲解' for row in rows),
            '静态确认关系对照': {'共同关系数': len(full_calls & narrow_calls),
                             '仅完整范围关系': sorted(full_calls - narrow_calls, key=str),
                             '仅缩小范围关系': sorted(narrow_calls - full_calls, key=str),
                             '边界': '目标为共同可见函数且调用所在文件在共同范围；按实际作用域、位置、关系类型匹配。范围变化可能改变确认能力；不直接判定缺陷，不包含未发布的全局AI判断。'},
            '完整范围全局概览': full['architecture']['overview'] if full['published'] else None,
            '缩小范围全局概览': narrow['architecture']['overview'] if narrow['published'] else None,
            '函数对照': rows, '尚缺': gaps + ['需要人工核对四部分概览、职责差异、条件语义和核心协作；字符串差异不等于语义冲突'],
            '验收通过': False}


def main():
    catalogue = json.loads((REPOSITORY / 'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    report = {'核对时间UTC': datetime.now(UTC).isoformat(), '模型请求': 0, '执行目标代码': False,
              '修改原始源码': False, '性质': '只读全/窄范围对照材料；未发布检查点仅用于局部核对，不计全局验收',
              '样本': {}}
    for sample in catalogue['repositories']:
        if sample['id'] in {'hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'}:
            full, narrow = (load_scope(sample['id'], scope, sample['commit']) for scope in ('full', 'narrow'))
            report['样本'][sample['id']] = compare(full, narrow)
    destination = REPORTS / '全窄范围源码与结果对照.json'
    if destination.exists():
        raw = destination.read_bytes()
        archive = destination.with_name(destination.stem + '-' + hashlib.sha256(raw).hexdigest() + '.json')
        if not archive.exists():
            archive.write_bytes(raw)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({sample: {key: item[key] for key in ('共同可见函数数', '完整范围检查点批数',
                     '共同函数在完整范围有唯一讲解数', '完整范围结果已发布', '验收通过')}
                     for sample, item in report['样本'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
