"""只读对照最新完整/缩小范围结果，保留真实文字供源码质量复核。"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from codescape.models import Analysis

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'
cases = {
    'hiredis-c': {'redisReaderGetReply', 'redisReaderFeed'},
    'flask-python': {'get_load_dotenv', '_prepare_send_file_kwargs', 'send_file', 'get_root_path'},
    'spdlog-cpp': {'sink_it_', 'log_', 'flush_', 'should_flush_'},
    'zod-typescript': {'finalizeParams', 'failure', 'validateFallback'},
}


def load(sample, scope):
    path = reports / f'{sample}-{scope}真实模型验证.json'
    document = json.loads(path.read_text(encoding='utf-8'))
    if document['状态'] != '结构检查通过，待源码质量复核':
        return document, None, {}, path
    root = Path(document['数据目录']).resolve(strict=True)
    root.relative_to(repository / '.runtime/public-model-validation')
    artifact = document['静态索引制品']
    index_path = Path(artifact['path']).resolve(strict=True)
    index_path.relative_to(root / 'validation-artifacts')
    raw = index_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == artifact['sha256']
    analysis = Analysis.model_validate_json(raw)
    source = root / 'owned-source'
    files = {}
    for name in document['实际文件']:
        target = (source / name).resolve(strict=True)
        target.relative_to(source)
        files[name] = target.read_bytes()
    visible = {entity.id for entity in analysis.entities if entity.visible}
    explained = {note['entity_id'] for note in document['architecture']['explanations']}
    owned = [identifier for module in document['architecture']['modules'] for identifier in module['entity_ids']]
    # 发布允许显式部分讲解；缺失是质量缺口，不能令对照中断或冒称全覆盖。
    assert explained <= visible
    assert visible == set(owned)
    assert len(owned) == len(set(owned))
    return document, analysis, files, path


def identity(entity):
    location = entity.location
    return (location.path, entity.name, location.start_line, location.end_line, entity.implementation_hash)


def excerpts(document, analysis, files, names):
    notes = {note['entity_id']: note for note in document['architecture']['explanations']}
    selected = []
    for entity in analysis.entities:
        if not entity.visible or entity.name.split('::')[-1] not in names:
            continue
        location = entity.location
        selected.append({
            '函数': entity.name, '位置': location.model_dump(),
            '源码': '\n'.join(files[location.path].decode('utf-8').splitlines()[location.start_line-1:location.end_line]),
            '实际讲解': notes.get(entity.id),
            '讲解状态': '有实际讲解，待源码复核' if entity.id in notes else '尚未生成讲解',
            '实际模块': [{key: module[key] for key in ('name', 'summary', 'incomplete')}
                       for module in document['architecture']['modules'] if entity.id in module['entity_ids']],
        })
    return selected


def main():
    result = {'核对时间UTC': datetime.now(UTC).isoformat(), '执行目标代码': False,
              '模型请求': 0, '样本': {}, '边界': '源码、身份、覆盖和真实说明对照；不以结构或文字相同判定语义质量通过。'}
    for sample, names in cases.items():
        full, large, files, full_path = load(sample, 'full')
        narrow, small, small_files, narrow_path = load(sample, 'narrow')
        item = {'完整状态': full['状态'], '缩小范围状态': narrow['状态'],
                '报告': {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (full_path, narrow_path)},
                '完整流水线': full['流水线'], '缩小范围流水线': narrow['流水线'],
                '完整接收端与模型': {'base_url': full['接收端'], 'model': full['模型']},
                '缩小范围接收端与模型': {'base_url': narrow['接收端'], 'model': narrow['模型']},
                '同一接收端与模型': (full['接收端'], full['模型']) == (narrow['接收端'], narrow['模型']),
                '同一模型流水线': full['流水线'] == narrow['流水线'],
                '语义质量结论': '待源码逐项复核，不能由本脚本自动判定。'}
        assert full['Commit'] == narrow['Commit']
        if small:
            visible_ids = {entity.id for entity in small.entities if entity.visible}
            explained_ids = {note['entity_id'] for note in narrow['architecture']['explanations']}
            item['缩小范围讲解覆盖'] = {'可见函数': len(visible_ids), '有讲解': len(explained_ids),
                '缺失函数': [entity.name for entity in small.entities if entity.id in visible_ids - explained_ids]}
            item['缩小范围抽查材料'] = excerpts(narrow, small, small_files, names)
            item['缩小范围模块摘要'] = [{key: module[key] for key in ('name', 'summary', 'incomplete')}
                                  for module in narrow['architecture']['modules']]
            item['缩小范围未完成模块'] = sum(module['incomplete'] for module in narrow['architecture']['modules'])
        if large and small and item['同一接收端与模型'] and item['同一模型流水线']:
            item['完整范围讲解覆盖'] = {
                '可见函数': sum(entity.visible for entity in large.entities),
                '有讲解': len(full['architecture']['explanations']),
                '缺失函数': [entity.name for entity in large.entities if entity.visible
                           and entity.id not in {note['entity_id'] for note in full['architecture']['explanations']}]}
            assert all(files.get(name) == raw for name, raw in small_files.items())
            identities = {identity(entity) for entity in large.entities if entity.visible}
            assert all(identity(entity) in identities for entity in small.entities if entity.visible)
            item.update(源码字节与实现身份一致=True,
                        完整可见函数=sum(entity.visible for entity in large.entities),
                        缩小范围可见函数=sum(entity.visible for entity in small.entities),
                        完整未完成模块=sum(module['incomplete'] for module in full['architecture']['modules']),
                        完整抽查材料=excerpts(full, large, files, names),
                        完整概览=full['architecture']['overview'],
                        缩小范围概览=narrow['architecture']['overview'])
        else:
            item['范围对照限制'] = '需两份均已发布且使用同一接收端、模型及流水线，才能生成本轮范围对照；局部材料单独保留。'
        result['样本'][sample] = item
    (reports / '公开工程最新范围对照材料.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({name: {key: value[key] for key in ('完整状态', '缩小范围状态')}
                      for name, value in result['样本'].items()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
