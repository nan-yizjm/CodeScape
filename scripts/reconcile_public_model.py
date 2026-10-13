"""核对中断的公开样本评测终态；只读数据库，补齐报告，不发模型请求。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from check_public_model import implementation_hash, saved_snapshot
from codescape.models import Analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True, choices=['hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'])
    parser.add_argument('--scope', required=True, choices=['full', 'narrow'])
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    path = repository / 'docs/reports/p0' / f'{args.id}-{args.scope}真实模型验证.json'
    record = json.loads(path.read_text(encoding='utf-8'))
    assert record['状态'] == '进行中', '只补齐尚未写入终态的评测报告。'
    root = Path(record['数据目录']).resolve(strict=True)
    root.relative_to(repository / '.runtime/public-model-validation')
    with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        project = dict(connection.execute('SELECT * FROM prototype_projects WHERE id=?',
                                          (record['project_id'],)).fetchone())
        assert project['status'] in {'ready', 'paused'}, '实际项目尚未到达终态，不能补齐。'
        assert project['version_id'] == record['version_id']
        assert not connection.execute("SELECT id FROM analysis_jobs WHERE project_id=? AND status='running'",
                                      (record['project_id'],)).fetchone()
        rows = [dict(row) for row in connection.execute('SELECT * FROM ai_requests')]
        assert all(row['status'] not in {'prepared', 'sent'} for row in rows), '仍有在途请求，不能补齐。'
        for row in rows:
            row['usage'] = json.loads(row['usage']) if row['usage'] else None
        progress = connection.execute('SELECT value FROM prototype_analysis_tasks WHERE project_id=?',
                                      (record['project_id'],)).fetchone()
        analysis = Analysis.model_validate_json(project['result'])
    assert record['实际快照hash'] == json.loads(project['snapshot'])['hash']
    sources = json.loads((repository / 'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    selected = next(item for item in sources['repositories'] if item['id'] == args.id)
    assert record['Commit'] == selected['commit']
    snapshot, manifest = saved_snapshot(selected)
    assert record['原始快照hash'] == manifest['hash']
    for name in record['实际文件']:
        owned = (root / 'owned-source' / name).resolve(strict=True)
        owned.relative_to(root / 'owned-source')
        original = (snapshot / name).resolve(strict=True)
        original.relative_to(snapshot)
        assert owned.read_bytes() == original.read_bytes(), '源码字节已变，不能补齐原评测身份。'
    path.with_name(path.stem + '-中断时-' + str(uuid.uuid4()) + '.json').write_bytes(path.read_bytes())
    raw = analysis.model_dump_json(indent=2).encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    artifacts = root / 'validation-artifacts'
    artifacts.mkdir(exist_ok=True)
    artifact = artifacts / (digest + '.json')
    if artifact.exists():
        assert artifact.read_bytes() == raw
    else:
        artifact.write_bytes(raw)
    record.update({
        '状态': '分析暂停' if project['status'] == 'paused' else '结构检查通过，待源码质量复核',
        '错误': project['error'], '进度': json.loads(progress[0]) if progress else record.get('进度'),
        'architecture': json.loads(project['architecture']) if project['architecture'] else None,
        '静态索引制品': {'path': str(artifact), 'sha256': digest, 'bytes': len(raw)},
        '请求账本': rows, '请求状态': dict(Counter(row['status'] for row in rows)),
        '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
        '未知用量请求': sum('total_tokens' not in (row['usage'] or {}) for row in rows), '费用': None,
        '中断报告核对时间UTC': datetime.now(UTC).isoformat(),
        '核对时原快照字节未变': True,
        '核对时实施代码sha256': implementation_hash(), '评测期间实施代码一致': None,
        '边界': '原报告中断后只读核对实际终态与账本，原中断报告另存；不重启分析、不发送源码、不把核对时的代码摘要冒充进程结束时验证或质量验收。',
    })
    record['核对时实施代码与起始一致'] = record['核对时实施代码sha256'] == record['实施代码起始sha256']
    diagnostics = root / 'validation-errors.jsonl'
    record['脱敏校验记录'] = [json.loads(line) for line in diagnostics.read_text(encoding='utf-8').splitlines()] if diagnostics.exists() else []
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    print(json.dumps({key: record[key] for key in ('状态', '进度', '请求状态', '已知total_tokens', '未知用量请求')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
