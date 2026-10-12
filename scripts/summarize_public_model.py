"""只读汇总公开工程评测进度；包含历史重测账本，不发起模型请求。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codescape.models import Analysis
from codescape.provider import MAX_CONTEXT_CHARS, PIPELINE_VERSION, Provider

repository = Path(__file__).resolve().parents[1]
reports = repository / 'docs/reports/p0'
samples = ('hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript')


def read_database(document):
    root = Path(document['数据目录']).resolve(strict=True)
    root.relative_to(repository / '.runtime/public-model-validation')
    with sqlite3.connect((root / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        rows = [dict(row) for row in connection.execute(
            'SELECT id,status,usage,http_status,error_code FROM ai_requests')]
        for row in rows:
            row['usage'] = json.loads(row['usage']) if row['usage'] else None
        project = connection.execute('SELECT status,error FROM prototype_projects WHERE id=?',
            (document.get('project_id'),)).fetchone()
        progress = connection.execute('SELECT value FROM prototype_analysis_tasks WHERE project_id=?',
            (document.get('project_id'),)).fetchone()
        return rows, dict(project) if project else None, json.loads(progress['value']) if progress else None


def main():
    destination = reports / '公开工程真实评测进度.json'
    summary = json.loads(destination.read_text(encoding='utf-8'))
    summary['日期'] = (datetime.now(UTC) + timedelta(hours=8)).date().isoformat()
    summary['汇总时间UTC'] = datetime.now(UTC).isoformat()
    receipts = {}
    documents = {}
    for sample in samples:
        for path in reports.glob(f'{sample}-*真实模型验证*.json'):
            document = json.loads(path.read_text(encoding='utf-8'))
            documents[path.name] = document
            for row in document.get('请求账本', []):
                receipts[row['id']] = row
    # 每个评测目录只读一次。历史报告的累计账本先去重，再由数据库补齐在途和新完成的请求。
    roots = {document['数据目录']: document for document in documents.values()}
    live = {}
    for root, document in roots.items():
        rows, project, progress = read_database(document)
        receipts.update({row['id']: row for row in rows})
        live[root] = rows, project, progress
    for sample in samples:
        for scope in ('full', 'narrow'):
            document = documents.get(f'{sample}-{scope}真实模型验证.json')
            if document is None:
                continue
            rows, project, progress = live[document['数据目录']]
            state = {'analyzing': '进行中', 'paused': '分析暂停',
                     'ready': '结构检查通过，待源码质量复核'}.get(
                         project['status'] if project else None, document['状态'])
            item = {
                'Commit': document['Commit'], '实际文件': document['实际文件'],
                '模型流水线': document.get('流水线'),
                '状态': state, '错误': project['error'] if project else document.get('错误'),
                '进度': progress or document.get('进度'),
                '请求状态': dict(Counter(row['status'] for row in rows)),
                '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
                '未知用量请求': sum(not row['usage'] for row in rows),
                '原快照字节未变': document.get('原快照字节未变'),
            }
            if scope == 'full':
                artifact = document.get('静态索引制品')
                if artifact:
                    root = Path(document['数据目录']).resolve(strict=True)
                    path = Path(artifact['path']).resolve(strict=True)
                    path.relative_to(root / 'validation-artifacts')
                    raw = path.read_bytes()
                    assert hashlib.sha256(raw).hexdigest() == artifact['sha256']
                    analysis = Analysis.model_validate_json(raw)
                    source = root / 'owned-source'
                    files = {}
                    for name in document['实际文件']:
                        target = (source / name).resolve(strict=True)
                        target.relative_to(source)
                        files[name] = target.read_text(encoding='utf-8')
                    # 仅计算本地上下文，不读取凭据、不重新解析源码、不调用模型。
                    planner = Provider.__new__(Provider)
                    batches = planner._plan_batches(analysis, files)
                    sizes = [len(planner._context(analysis, files_, ranges)[0])
                             for files_, ranges in batches]
                    item['修订后本地批次检查'] = {
                        '批次数': len(batches), '最大上下文字符': max(sizes, default=0),
                        '超过180000字符': sum(size > MAX_CONTEXT_CHARS for size in sizes),
                        '仅静态规划': True, '规划流水线': PIPELINE_VERSION,
                        '索引所属模型流水线': document.get('流水线'),
                        '索引sha256': artifact['sha256'],
                    }
                summary['完整范围'][sample] = item
            else:
                old = summary['缩小范围'].setdefault(sample, {})
                # 已有源码复核结论属于旧结果；重测不能自动覆盖为质量通过。
                old['最新重测'] = item
    rows = list(receipts.values())
    summary['账本汇总'] = {
        '去重请求数': len(rows),
        '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
        '缺少用量请求数': sum(not row['usage'] for row in rows),
        '在途请求数': sum(row['status'] in {'prepared', 'sent'} for row in rows),
        '费用': None,
        '说明': '包含历史失败、截断、修复及重新评测，按请求ID去重；在途属于尚未返回用量，未知不记零，缺少价格不估算费用。',
    }
    balances = [value['错误'] == '服务余额不足。' for value in summary['完整范围'].values()]
    balance_path = reports / '真实模型账户可用状态核对.json'
    available = (json.loads(balance_path.read_text(encoding='utf-8')).get('账户可供API调用')
                 if balance_path.exists() else None)
    summary['外部阻塞'] = ('账户只读核对已恢复可用，已开始新修订真实重测；历史HTTP402检查点保留，实际进度见各样本记录。'
                         if available is True else
                         '官方账户只读查询确认暂不可调用，不重试收费任务；各样本的暂停原因和已发布状态见实际记录。'
                         if available is False else
                         '四个完整范围均因官方HTTP402余额不足暂停；检查点和历史报告保留，当前不继续重试收费任务。'
                         if all(balances) else '具体错误及在途状态见各样本实际记录；此前HTTP402记录完整保留。')
    summary['验收结论'] = ('修订后的四个完整范围均因HTTP402暂停，完整质量、范围对照与用户验收未完成；'
                       '缩小范围源码复核及历史缺陷见各样本记录，结构发布不代表质量通过。'
                       if all(balances) and available is not True else '真实评测与源码质量复核尚未完成；结构发布不代表质量通过，不能宣称全部P0完成。')
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)
    print(json.dumps({'完整范围': {key: value['进度'] for key, value in summary['完整范围'].items()},
        '账本汇总': summary['账本汇总']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
