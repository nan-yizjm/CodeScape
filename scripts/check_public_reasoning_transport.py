"""一次隔离推理强度传输对照；仅固定公开样本，不发布结果、不执行目标代码。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from check_public_model import saved_snapshot
from codescape.context_tools import ArchitectureReply
from codescape.database import Database, now
from codescape.ledger import RequestLedger, ai_scope, known_usage
from codescape.models import Analysis, Architecture
from codescape.prompts import architecture_prompt
from codescape.provider import Provider
from codescape.streaming import read_json_response

repository = Path(__file__).resolve().parents[1]


async def main(sample):
    reports = repository / 'docs/reports/p0'
    prior = json.loads((reports / f'{sample}-full真实模型验证.json').read_text(encoding='utf-8'))
    catalogue = json.loads((repository / 'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    approved = next(item for item in catalogue['repositories'] if item['id'] == sample)
    assert prior['状态'] == '分析暂停' and prior['Commit'] == approved['commit']
    assert prior['接收端'] == 'https://www.cctq.ai/v1' and prior['模型'] == 'gpt-6.1-sol'
    original = Path(prior['数据目录']).resolve(strict=True)
    original.relative_to(repository / '.runtime/public-model-validation')
    frozen, _ = saved_snapshot(approved)
    files = {name: (original / 'owned-source' / name).read_text(encoding='utf-8') for name in prior['实际文件']}
    assert all((frozen / name).read_bytes() == (original / 'owned-source' / name).read_bytes() for name in files)
    checkpoint_path = original / 'projects' / prior['project_id'] / 'architecture-checkpoint.json'
    raw_checkpoint = checkpoint_path.read_bytes()
    saved = json.loads(raw_checkpoint)
    with sqlite3.connect((original / 'app.db').as_uri() + '?mode=ro', uri=True) as connection:
        analysis = Analysis.model_validate_json(connection.execute(
            'SELECT result FROM prototype_projects WHERE id=?', (prior['project_id'],)).fetchone()[0])
    planner = Provider.__new__(Provider)
    batches, reused = planner._execution_batches(analysis, files,
        [Architecture.model_validate(item) for item in saved['batches']])
    assert len(reused) == len(saved['batches']) < len(batches)
    batch, ranges = batches[len(reused)]
    context, _ = planner._context(analysis, batch, ranges)
    root = repository / '.runtime/public-model-validation' / str(uuid.uuid4())
    root.mkdir()
    with sqlite3.connect((original / 'app.db').as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(root / 'app.db') as destination:
            source.backup(destination)
    for name in ('provider.json', 'consents.json'):
        shutil.copyfile(original / name, root / name)
    (root / 'credentials').mkdir()
    shutil.copyfile(original / 'credentials/provider.dpapi', root / 'credentials/provider.dpapi')
    database = Database(root)
    provider = Provider(root, database)
    config = provider.settings()
    assert config['base_url'] == prior['接收端'] and config['model'] == prior['模型']
    job_id = str(uuid.uuid4())
    database.insert_job({'id': job_id, 'project_id': prior['project_id'], 'version_id': prior['version_id'],
        'kind': 'analysis', 'status': 'running', 'position': 0, 'created_at': now(), 'updated_at': now(),
        'detail': '一次推理强度传输对照，不发布结果'})
    system = architecture_prompt(ArchitectureReply.model_json_schema()) + (
        f'本次是第 {len(reused)+1}/{len(batches)} 批，只能为本批 entities 中的函数分配模块。')
    payload = {'model': config['model'], 'messages': [{'role': 'system', 'content': system},
        {'role': 'user', 'content': context}], 'response_format': {'type': 'json_object'},
        'max_tokens': 4096, 'stream': False, 'reasoning_effort': 'low'}
    record = {key: prior[key] for key in ('样本', 'Commit', '接收端', '模型', '流水线', '实际快照hash')}
    record.update({'核对时间UTC': datetime.now(UTC).isoformat(), '数据目录': str(root),
        '实际文件': list(batch), '最多请求': 1, '执行目标代码': False, '状态': '进行中',
        'reasoning_effort': 'low', '批次': len(reused) + 1, '原检查点sha256': hashlib.sha256(raw_checkpoint).hexdigest(),
        '参数规范参考': 'https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create',
        '边界': '中转站是否支持或采用该可选参数未知，以实际对照记录为准。仅传输与schema核对，不发布架构或计语义质量通过；没有改变主工作区。'})
    report = reports / f'{sample}-reasoning-low-transport真实模型验证-{uuid.uuid4()}.json'
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
    ledger = RequestLedger(database)
    identifier, sent, usage = None, False, None
    started = time.monotonic()
    try:
        with ai_scope(project_id=prior['project_id'], version_id=prior['version_id'], job_id=job_id,
                      request_config=config, phase='architecture', source_chars=sum(map(len, batch.values()))):
            provider.verify_scope(database, config)
            identifier = ledger.prepare(config, payload, str(uuid.uuid4()), 1)
            key = provider.credentials.read()
            assert key
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=15), trust_env=False,
                                         follow_redirects=False) as client:
                provider.verify_scope(database, config)
                ledger.sent(identifier)
                sent = True
                async with asyncio.timeout(600):
                    async with client.stream('POST', config['base_url'].rstrip('/') + '/chat/completions',
                        json=payload, headers={'Authorization': f'Bearer {key}'}) as response:
                        document = await read_json_response(response)
                        status = response.status_code
            usage = known_usage(document.get('usage')) if isinstance(document, dict) else None
            record['HTTP状态'] = status
            if status != 200:
                ledger.finish(identifier, 'failed', usage=usage, http_status=status, error_code=f'HTTP_{status}')
                record['状态'] = '对照参数或服务请求失败'
            else:
                try:
                    choice = document['choices'][0]
                    assert choice['finish_reason'] != 'length'
                    candidate = json.loads(choice['message']['content'])
                    ArchitectureReply.model_validate(candidate)
                except (ValueError, KeyError, IndexError, TypeError, AssertionError):
                    ledger.finish(identifier, 'failed', usage=usage, http_status=status, error_code='INVALID_CONTENT')
                    record['状态'] = '已收到响应，未通过完整schema检查'
                else:
                    ledger.finish(identifier, 'success', usage=usage, http_status=status)
                    record['状态'] = 'JSON及schema接收通过，仅传输对照'
                    record['候选内容sha256'] = hashlib.sha256(json.dumps(candidate, ensure_ascii=False,
                        sort_keys=True).encode()).hexdigest()
    except (httpx.HTTPError, TimeoutError):
        if identifier:
            ledger.finish(identifier, 'outcome_unknown' if sent else 'failed', usage=usage,
                          error_code='TRANSPORT_UNKNOWN' if sent else 'NOT_SENT')
        record['状态'] = '传输结果未知，未重试'
    except Exception:
        if identifier:
            ledger.finish(identifier, 'failed', usage=usage, error_code='LOCAL_OR_CONTENT_ERROR')
        record['状态'] = '本地或响应检查未通过，未重试'
    finally:
        database.update_job(job_id, status='paused', detail='单次对照已停止，不发布结果')
        summary = ledger.summary()
        rows = [row for row in summary['records'] if row['id'] == identifier]
        assert len(rows) <= 1 and checkpoint_path.read_bytes() == raw_checkpoint
        record.update({'请求账本': rows, '已知total_tokens': sum((row['usage'] or {}).get('total_tokens', 0) for row in rows),
            '未知用量请求': sum('total_tokens' not in (row['usage'] or {}) for row in rows),
            '耗时秒': round(time.monotonic() - started, 3), '原检查点未改写': True})
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')
        database.engine.dispose()
        print(json.dumps({key: record[key] for key in ('状态', '耗时秒', '已知total_tokens', '未知用量请求')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', required=True, choices=['hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript'])
    asyncio.run(main(parser.parse_args().id))
