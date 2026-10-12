"""仅发送一次已授权公开样本的当前批次，记录脱敏传输阶段，不发布结果。"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from codescape.context_tools import ArchitectureReply
from codescape.database import Database, now
from codescape.ledger import ai_scope
from codescape.models import Analysis
from codescape.prompts import architecture_prompt
from codescape.provider import PIPELINE_VERSION, Provider, ProviderError
from codescape.snapshots import read_snapshot
from sqlalchemy import select

SAMPLES = ('hiredis-c', 'spdlog-cpp', 'flask-python', 'zod-typescript')
ERROR_TYPES = frozenset({
    'TimeoutError', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout',
    'ConnectError', 'ReadError', 'WriteError', 'CloseError', 'RemoteProtocolError',
    'LocalProtocolError', 'ProxyError', 'UnsupportedProtocol', 'HTTPError', 'OSError',
    'SSLError', 'SSLCertVerificationError', 'ConnectionResetError', 'BrokenPipeError',
})
PHASES = frozenset({
    'connection.connect_tcp', 'connection.start_tls',
    'http11.send_request_headers', 'http11.send_request_body',
    'http11.receive_response_headers', 'http11.receive_response_body',
    'http11.response_closed', 'connection.close',
})


def safe_error_chain(error):
    """只保留程序类型白名单；不序列化异常信息、URL、请求或服务响应。"""
    result, seen = [], set()
    while error is not None and id(error) not in seen and len(result) < 8:
        seen.add(id(error))
        name = type(error).__name__
        result.append(name if name in ERROR_TYPES else '未识别异常类型')
        error = error.__cause__ or error.__context__
    return result


async def diagnose(sample, json_response):
    repository = Path(__file__).resolve().parents[1]
    reports = repository / 'docs/reports/p0'
    prior = json.loads((reports / f'{sample}-full真实模型验证.json').read_text(encoding='utf-8'))
    catalogue = json.loads((repository / 'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    approved = next(item for item in catalogue['repositories'] if item['id'] == sample)
    assert prior['Commit'] == approved['commit'] and prior['样本'] == sample
    assert prior['状态'] == '分析暂停'
    assert prior['接收端'] == 'https://www.cctq.ai/v1' and prior['模型'] == 'gpt-6-luna'
    # 本轮仅可使用相同流水线的检查点，不借诊断迁移旧生成结果。
    if prior['流水线'] != PIPELINE_VERSION:
        raise ValueError('历史评测与当前流水线不同，不能借传输诊断复用旧生成结果。')
    original = Path(prior['数据目录']).resolve(strict=True)
    original.relative_to(repository / '.runtime/public-model-validation')
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
    job_id = str(uuid.uuid4())
    record = {key: prior[key] for key in ('样本', 'Commit', '接收端', '模型', '流水线',
                                         '实际快照hash', 'project_id', 'version_id')}
    record.update({'日期': datetime.now().date().isoformat(), '核对时间UTC': datetime.now(UTC).isoformat(),
        '数据目录': str(root), '本次新建job_id': job_id, '最多发送': 1, '执行目标代码': False,
        '状态': '进行中', '传输阶段': [], '异常类型链': [], '边界':
        '只诊断当前暂停批次的传输，不发布架构；不把JSON接收成功计为质量通过。异常正文、请求头和密钥不记录。'})
    report = reports / f'{sample}-bounded-transport-{uuid.uuid4()}真实模型验证.json'
    project = database.get(prior['project_id'])
    assert project['status'] == 'paused' and project['version_id'] == prior['version_id']
    analysis = Analysis.model_validate(project['result'])
    snapshot_root = original / 'projects' / project['id'] / 'snapshots' / project['snapshot']['id']
    files = read_snapshot(snapshot_root, project['snapshot'])
    assert set(files) == set(prior['实际文件']) and project['snapshot']['hash'] == prior['实际快照hash']
    hashes = {name: hashlib.sha256((original / 'owned-source' / name).read_bytes()).hexdigest() for name in files}
    assert all((original / 'owned-source' / name).read_bytes() == (snapshot_root / name).read_bytes()
               for name in files)
    provider = Provider(root, database)
    config = provider.settings()
    assert config['base_url'] == prior['接收端'] and config['model'] == prior['模型']
    if json_response:
        provider.configure(config['base_url'], config['model'], None, False)
    batches = provider._plan_batches(analysis, files)
    index = prior['进度']['completed']
    assert len(batches) == prior['进度']['total'] and index < len(batches)
    batch, ranges = batches[index]
    context, _ = provider._context(analysis, batch, ranges)
    record.update({'批次': index + 1, '总批次': len(batches), '实际文件': list(batch),
                   '上下文字符': len(context), '源码字符': sum(map(len, batch.values())),
                   '流式接收': provider.settings()['stream_response']})
    database.insert_job({'id': job_id, 'project_id': project['id'], 'version_id': project['version_id'],
        'kind': 'analysis', 'status': 'running', 'position': 0, 'created_at': now(), 'updated_at': now(),
        'detail': '一次公开样本传输阶段诊断，不发布结果'})
    original_send = httpx.AsyncClient.send
    started = time.monotonic()

    async def trace(event, info):
        phase, _, outcome = event.rpartition('.')
        if phase in PHASES and outcome in {'started', 'complete', 'failed'}:
            item = {'阶段': phase, '结果': outcome, '耗时秒': round(time.monotonic() - started, 3)}
            if outcome == 'failed':
                item['异常类型链'] = safe_error_chain(info.get('exception'))
            record['传输阶段'].append(item)
            report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')

    async def observed_send(client, request, *args, **kwargs):
        request.extensions['trace'] = trace
        record['请求体UTF8字节'] = len(request.content)
        record['HTTP请求体sha256'] = hashlib.sha256(request.content).hexdigest()
        try:
            return await original_send(client, request, *args, **kwargs)
        except (httpx.HTTPError, TimeoutError) as error:
            record['异常类型链'] = safe_error_chain(error)
            raise

    try:
        httpx.AsyncClient.send = observed_send
        with ai_scope(project_id=project['id'], version_id=project['version_id'], job_id=job_id,
                      request_config=provider.settings(), phase='architecture',
                      source_chars=sum(map(len, batch.values()))):
            await provider.request(architecture_prompt(ArchitectureReply.model_json_schema())
                + f'本次是第 {index+1}/{len(batches)} 批，只能为本批 entities 中的函数分配模块。',
                context, max_tokens=8000, max_attempts=1)
        record['状态'] = 'JSON对象接收成功，仅传输诊断'
    except ProviderError as error:
        record['状态'] = '传输诊断失败'
        record['错误'] = str(error)
    except BaseException:
        record['状态'] = '本地诊断中止'
        raise
    finally:
        httpx.AsyncClient.send = original_send
        database.update_job(job_id, status='completed', detail='单次诊断已结束；不发布架构')
        with database.engine.connect() as connection:
            rows = [dict(row) for row in connection.execute(select(database.requests)
                    .where(database.requests.c.job_id == job_id)).mappings()]
        assert len(rows) <= 1
        record.update({'请求账本': rows, '请求状态': dict(Counter(row['status'] for row in rows)),
            '已知total_tokens': sum((row.get('usage') or {}).get('total_tokens', 0) for row in rows),
            '未知用量请求': sum(row.get('usage') is None for row in rows),
            '原源码字节未变': all(hashlib.sha256((original / 'owned-source' / name).read_bytes()).hexdigest()
                                   == checksum for name, checksum in hashes.items()),
            '耗时秒': round(time.monotonic() - started, 3)})
        previous_rows = [row for row in prior.get('请求账本', []) if row.get('phase') == 'architecture']
        record['生产最近请求payload_hash'] = previous_rows[-1]['payload_hash'] if previous_rows else None
        record['与生产最近请求payload_hash一致'] = (
            rows[0]['payload_hash'] == record['生产最近请求payload_hash'] if rows and previous_rows else None)
        report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        database.engine.dispose()
        print(json.dumps({key: record[key] for key in ('状态', '批次', '异常类型链', '请求状态',
                                                      '已知total_tokens', '未知用量请求')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', choices=SAMPLES, required=True)
    parser.add_argument('--json-response', action='store_true', help='只在隔离诊断中使用非流式JSON')
    args = parser.parse_args()
    asyncio.run(diagnose(args.id, args.json_response))
