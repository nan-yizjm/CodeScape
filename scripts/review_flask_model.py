"""只读复核固定 Flask 真实结果与默认条件，不执行目标代码或调用模型。"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from codescape.models import Analysis
from codescape.provider import PIPELINE_VERSION
from codescape.semantic_facts import defaulted_flag_facts

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'


def load_index(document):
    root = Path(document['数据目录']).resolve(strict=True)
    root.relative_to(repository / '.runtime/public-model-validation')
    artifact = document['静态索引制品']
    path = Path(artifact['path']).resolve(strict=True)
    path.relative_to(root / 'validation-artifacts')
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == artifact['sha256']
    analysis = Analysis.model_validate_json(raw)
    files = {}
    for name in document['实际文件']:
        source = (root / 'owned-source' / name).resolve(strict=True)
        source.relative_to(root / 'owned-source')
        files[name] = source.read_text(encoding='utf-8')
    return root, analysis, files


def identity(entity):
    return (entity.location.path, entity.name, entity.location.start_line, entity.location.end_line,
            entity.implementation_hash)


def main():
    full_path = reports / 'flask-python-full真实模型验证.json'
    narrow_path = reports / 'flask-python-narrow真实模型验证.json'
    full = json.loads(full_path.read_text(encoding='utf-8'))
    narrow = json.loads(narrow_path.read_text(encoding='utf-8'))
    assert full['Commit'] == narrow['Commit'] == 'd086db856be187255b8ec61ef409357393020f32'
    assert full['architecture'] and not narrow['architecture']
    full_root, index, files = load_index(full)
    root, small, small_files = load_index(narrow)
    assert all(files[name] == text for name, text in small_files.items())
    assert all((full_root / 'owned-source' / name).read_bytes() ==
               (root / 'owned-source' / name).read_bytes() for name in small_files)
    identities = {identity(entity) for entity in index.entities if entity.visible}
    assert all(identity(entity) in identities for entity in small.entities if entity.visible)
    architecture = full['architecture']
    visible = {entity.id: entity for entity in index.entities if entity.visible}
    notes = {item['entity_id']: item for item in architecture['explanations']}
    assert set(notes) == set(visible)
    checkpoint = root / 'projects' / narrow['project_id'] / 'architecture-checkpoint.json'
    raw_checkpoint = checkpoint.read_bytes()
    partial = json.loads(raw_checkpoint)
    assert len(partial['batches']) == narrow['进度']['completed'] == 1
    first = partial['batches'][0]
    key = next(entity for entity in small.entities if entity.name == 'get_load_dotenv')
    old_key = next(entity for entity in index.entities if entity.name == 'get_load_dotenv')
    fact = defaulted_flag_facts(small, small_files)[key.id]
    old_module = next(item for item in architecture['modules'] if old_key.id in item['entity_ids'])
    new_module = next(item for item in first['modules'] if key.id in item['entity_ids'])
    new_note = next(item for item in first['explanations'] if item['entity_id'] == key.id)
    checks = []
    for name in ('handle_user_exception', 'dispatch_request', 'full_dispatch_request'):
        entity = next(item for item in index.entities
                      if item.name == name and item.location.path == 'src/flask/app.py')
        location = entity.location
        snippet = '\n'.join(files[location.path].splitlines()[location.start_line - 1:location.end_line])
        checks.append({'函数': name, '位置': location.model_dump(), '源码': snippet,
                       '实际说明': notes[entity.id]})
    document = {
        '核对时间UTC': datetime.now(UTC).isoformat(), '样本': 'flask-python', 'Commit': full['Commit'],
        '性质': '固定公开源码与真实模型结果只读对照，不执行源码，不调用模型',
        '完整报告': full_path.name, '完整报告sha256': hashlib.sha256(full_path.read_bytes()).hexdigest(),
        '完整模型流水线': full['流水线'], '完整范围可见函数': len(visible), '完整讲解': len(notes),
        '未完成归属模块': sum(item['incomplete'] for item in architecture['modules']),
        '全局说明': {key: value for key, value in architecture['overview'].items() if key != 'analysis_notes'},
        '范围对照': {'缩小范围可见函数': sum(item.visible for item in small.entities),
                     '同Commit源码字节文本与实现身份一致': True,
                     '边界': '只确认索引覆盖；新缩小范围仅1/2批，未发布，不能计完整语义范围对照通过。'},
        '完整结果抽查': checks,
        '完整摘要默认分支问题': {'模块': old_module, '详情': notes[old_key.id],
                               '结论': '模块摘要只提否定值加载，遗漏未设置或空值返回default；详情正确不消除摘要缺口。'},
        '新缩小范围局部验证': {
            '报告': narrow_path.name, '流水线': narrow['流水线'], '状态': narrow['状态'],
            '进度': narrow['进度'], '检查点sha256': hashlib.sha256(raw_checkpoint).hexdigest(),
            '检查点模型流水线': partial['pipeline_version'], '源码事实': fact,
            '实际模块摘要': new_module, '实际函数说明': new_note,
            '结论': '第一批摘要已保留调用方default及默认True；这里只记录局部改善，第二批HTTP402暂停，未发布完整结果。'},
        '本地事实实现版本': PIPELINE_VERSION,
        '验收结论': '完整结构已发布且覆盖341个可见函数；已知摘要错误和12个未完成归属模块尚在，不计整体质量通过。',
    }
    destination = reports / 'Flask完整结果与默认条件局部验证.json'
    destination.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'完整函数': len(visible), '未完成模块': document['未完成归属模块'],
                      '缩小范围进度': narrow['进度']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
