"""重用固定102文件快照检查完整C++解析边界，不执行目标、不调用模型。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from codescape.index_process import index_snapshot
from codescape.provider import PIPELINE_VERSION

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'


def main():
    previous_path = reports / 'spdlog修订后产品静态索引验证.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8'))
    root = Path(previous['数据目录']).resolve(strict=True)
    root.relative_to(repository / '.runtime/cpp8-product-validation')
    with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT snapshot,version_id,result,architecture FROM prototype_projects WHERE id=?',
                                 (previous['project_id'],)).fetchone()
    assert row and row[1] == previous['version_id']
    manifest = json.loads(row[0])
    snapshot = root / 'projects' / previous['project_id'] / 'snapshots' / manifest['id']
    snapshot = snapshot.resolve(strict=True)
    snapshot.relative_to(root)
    public_path = reports / 'spdlog-cpp-full真实模型验证.json'
    public = json.loads(public_path.read_text(encoding='utf-8'))
    assert len(manifest['files']) == 102 and public['Commit'] == previous['Commit']
    assert manifest['hash'] == public['实际快照hash']
    assert set(manifest['files']) == set(public['实际文件'])
    assert previous['Commit'] == 'd6c93f38a08b4ea0f8ffcfd7f7857307d5d5c3fa'
    assert not any('/fmt/bundled/' in path for path in manifest['files'])
    before = {name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest() for name in manifest['files']}
    destination = repository / '.runtime/index-diagnostics-validation' / str(uuid.uuid4())
    destination.mkdir(parents=True, exist_ok=False)
    print('固定102文件、120秒完整解析上限；未执行源码，未请求模型。', flush=True)
    started = time.perf_counter()
    analysis = asyncio.run(index_snapshot(snapshot, manifest, row[1], timeout=120))
    elapsed = time.perf_counter() - started
    after = {name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest() for name in manifest['files']}
    assert before == after
    with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        assert row == connection.execute('SELECT snapshot,version_id,result,architecture FROM prototype_projects WHERE id=?',
                                         (previous['project_id'],)).fetchone()
    artifact = destination / 'analysis.json'
    artifact.write_text(analysis.model_dump_json(), encoding='utf-8')
    missing = [item for item in analysis.diagnostics if item.category == 'missing_dependency']
    for item in missing:
        if item.path is not None:
            assert item.path in manifest['files']
            if item.line is not None:
                assert 0 < item.line <= len((snapshot / item.path).read_text(encoding='utf-8').splitlines())
        else:
            assert item.line is None
    bundled = [item for item in missing if 'spdlog/fmt/bundled/format.h' in item.message]
    assert not bundled or all(item.path == 'include/spdlog/fmt/fmt.h' and item.line == 23 for item in bundled)
    errors = [item.model_dump() for item in analysis.diagnostics if item.category.startswith('native_index_')]
    fallback = any(item['category'] == 'native_index_incomplete' for item in errors)
    if fallback:
        assert not any(call.resolution == 'confirmed' for call in analysis.calls)
        assert any(item['category'] != 'native_index_incomplete' for item in errors)
    sink = next(entity for entity in analysis.entities if entity.visible and entity.name.split('::')[-1] == 'sink_it_'
                and entity.location.path == 'include/spdlog/logger-inl.h')
    assert (sink.location.start_line, sink.location.end_line) == (135, 146)
    assert any(call.caller_scope_id == sink.scope_id and call.location.start_line == 144 for call in analysis.calls)
    record = {
        '核对时间UTC': datetime.now(UTC).isoformat(), 'Commit': previous['Commit'], '快照hash': manifest['hash'],
        '原报告': previous_path.name, '原报告sha256': hashlib.sha256(previous_path.read_bytes()).hexdigest(),
        '固定Commit归因': {'报告': public_path.name, '报告sha256': hashlib.sha256(public_path.read_bytes()).hexdigest(),
                        '实际快照hash与文件集合一致': True},
        '流水线': PIPELINE_VERSION, '完整解析时间上限秒': 120, '实际总耗时秒': round(elapsed, 3),
        '文件数': len(before), '可见函数数': sum(entity.visible for entity in analysis.entities),
        '已确认调用数': sum(call.resolution == 'confirmed' for call in analysis.calls),
        '回退': fallback, '实际边界诊断': errors, '所有诊断分类': dict(Counter(item.category for item in analysis.diagnostics)),
        '缺失依赖诊断样本': [item.model_dump() for item in missing[:8]],
        'bundled_fmt实际出错位置': sorted({(item.path, item.line) for item in bundled}),
        '诊断锚点仅来自快照文件': True,
        '工具': analysis.tools, '完整尾部作用域': True, '源码字节未变': True, '旧项目与结果逐项不变': True,
        '执行目标代码': False, '模型请求': 0, '上传源码': False,
        '静态索引制品': {'path': str(artifact), 'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                       'bytes': artifact.stat().st_size},
        '边界': '仅记录本次新解析的已观察原因，不倒推历史原因；语法回退不作为原生确认或完成AI架构。缺失依赖时0条原生确认不代表无依赖；bundled fmt仍按授权样本范围排除，未自动增加上传范围。',
    }
    (reports / 'spdlog完整范围回退原因验证.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: record[key] for key in ('实际总耗时秒', '可见函数数', '已确认调用数', '实际边界诊断')},
                     ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
