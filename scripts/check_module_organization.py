"""使用已授权样本的已校验结果验证全局职责整理；隔离数据，不重跑源码批次、不发布。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from check_public_model import implementation_hash
from codescape.call_judgments import reconcile_judgments
from codescape.database import Database, now
from codescape.ledger import ai_scope
from codescape.models import Analysis, Architecture
from codescape.provider import (
    LEGACY_SOURCE_PIPELINE_VERSION,
    PIPELINE_VERSION,
    SOURCE_PIPELINE_VERSION,
    Provider,
    ProviderError,
)


async def main(args):
    repository = Path(__file__).resolve().parents[1]
    reports = repository / 'docs/reports/p0'
    origin_path = reports / f'{args.id}-narrow真实模型验证.json'
    prior = json.loads(origin_path.read_text(encoding='utf-8'))
    approved = next(item for item in json.loads((repository / 'docs/benchmark_sources.json').read_text(
        encoding='utf-8'))['repositories'] if item['id'] == args.id)
    assert prior['Commit'] == approved['commit'] and prior['状态'] == '结构检查通过，待源码质量复核'
    assert prior['流水线'] in {LEGACY_SOURCE_PIPELINE_VERSION, LEGACY_SOURCE_PIPELINE_VERSION + '-organization/1',
                            SOURCE_PIPELINE_VERSION, PIPELINE_VERSION}
    original = Path(prior['数据目录']).resolve(strict=True)
    original.relative_to(repository / '.runtime/public-model-validation')
    index_path = Path(prior['静态索引制品']['path']).resolve(strict=True)
    index_path.relative_to(original / 'validation-artifacts')
    raw = index_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == prior['静态索引制品']['sha256']
    analysis = Analysis.model_validate_json(raw)
    architecture = Architecture.model_validate(prior['architecture'])
    root = repository / '.runtime/public-model-validation' / str(uuid.uuid4())
    root.mkdir()
    with sqlite3.connect((original / 'app.db').as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(root / 'app.db') as destination:
            source.backup(destination)
    shutil.copyfile(original / 'consents.json', root / 'consents.json')
    actual = Path(os.environ['LOCALAPPDATA']) / 'CodeScape'
    config = Provider(actual).settings()
    assert config['base_url'] == prior['接收端'] == 'https://www.cctq.ai/v1'
    assert config['model'] == prior['模型'] == args.model
    shutil.copyfile(actual / 'provider.json', root / 'provider.json')
    (root / 'credentials').mkdir()
    shutil.copyfile(actual / 'credentials/provider.dpapi', root / 'credentials/provider.dpapi')
    database = Database(root)
    provider = Provider(root, database)
    job_id = str(uuid.uuid4())
    project = database.get(prior['project_id'])
    assert project['version_id'] == prior['version_id'] and project['snapshot']['hash'] == prior['实际快照hash']
    database.insert_job({'id': job_id, 'project_id': prior['project_id'], 'version_id': prior['version_id'],
        'kind': 'analysis', 'status': 'running', 'position': 0, 'created_at': now(), 'updated_at': now(),
        'detail': '隔离验证全局职责整理，不发布或更改原结果'})
    record = {key: prior[key] for key in ('样本', 'Commit', '接收端', '模型', '实际文件', 'project_id', 'version_id')}
    record.update({'数据目录': str(root), '流水线': PIPELINE_VERSION, '原报告流水线': prior['流水线'],
        '源码生成阶段': prior['流水线'].removesuffix('-organization/1'), '核对时间UTC': datetime.now(UTC).isoformat(),
        '原报告sha256': hashlib.sha256(origin_path.read_bytes()).hexdigest(), '新建job_id': job_id,
        '实施代码起始sha256': implementation_hash(), '原始源码发送': False, '执行目标代码': False,
        '实际快照hash': prior['实际快照hash'], '性质': '真实模型全局职责整理，使用已有验证数据，不重新生成源码讲解；不是完整工程或发布验收',
        '整理前模块': [module.model_dump() for module in architecture.modules]})
    try:
        with ai_scope(project_id=prior['project_id'], version_id=prior['version_id'], job_id=job_id,
                      request_config=config):
            async with asyncio.timeout(args.max_seconds):
                organized, _ = await provider.organize_modules(architecture, reconcile_judgments(analysis, architecture))
                record['整理映射'] = getattr(provider, '_organization_plan', None)
                organized.overview, _ = await provider.project_overview(organized, reconcile_judgments(analysis, organized))
        record.update({'状态': '全局职责整理校验通过，待源码质量复核；未发布',
                       'architecture': organized.model_dump(),
                       '原讲解与依据不变': organized.explanations == architecture.explanations,
                       '原调用判断不变': organized.call_judgments == architecture.call_judgments,
                       '原主要依赖依据不变': organized.primary_dependencies == architecture.primary_dependencies})
    except TimeoutError:
        record['状态'] = '职责整理达到开发评测时限，未发布；在途结果可能未知'
    except ProviderError as error:
        record.update({'状态': '职责整理未完成，原结果保持', '错误': str(error)})
    finally:
        database.update_job(job_id, status='completed', detail='隔离整理评测结束，未发布')
        with database.engine.connect() as connection:
            from sqlalchemy import select
            rows = [dict(row) for row in connection.execute(select(database.requests).where(
                database.requests.c.job_id == job_id)).mappings()]
        record.update({'请求账本': rows, '请求状态': dict(Counter(row['status'] for row in rows)),
            '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
            '未知用量请求': sum('total_tokens' not in (row['usage'] or {}) for row in rows), '费用': None,
            '实施代码结束sha256': implementation_hash()})
        record['评测期间实施代码一致'] = record['实施代码起始sha256'] == record['实施代码结束sha256']
        assert hashlib.sha256(origin_path.read_bytes()).hexdigest() == record['原报告sha256']
        record['原报告未变'] = True
        path = reports / f'{args.id}-organization-narrow真实模型验证-{uuid.uuid4()}.json'
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        database.engine.dispose()
        print(json.dumps({key: record[key] for key in ('状态', '请求状态', '已知total_tokens', '未知用量请求')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', choices=['hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'], required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--max-seconds', type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 1800:
        parser.error('开发评测等待上限需在1至1800秒之间。')
    asyncio.run(main(args))
