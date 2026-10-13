"""已授权的四个固定公开源码样本：运行产品导入、分析与账本路径，不执行目标代码。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from codescape.app import create_app
from codescape.database import Database
from codescape.provider import PIPELINE_VERSION, Provider
from fastapi.testclient import TestClient

repository = Path(__file__).resolve().parents[1]
reports = repository/'docs/reports/p0'


def implementation_hash():
    digest = hashlib.sha256()
    for path in sorted((repository/'server/codescape').rglob('*.py')):
        digest.update(path.relative_to(repository).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def saved_snapshot(selected):
    if selected['id'] == 'hiredis-c':
        record = json.loads((reports/'C工程语义解析验证.json').read_text(encoding='utf-8'))
        root = Path(record['data_root'])
        candidates = list((root/'projects').glob(f"*/snapshots/{record['snapshot_id']}"))
        assert len(candidates) == 1
        path = candidates[0]
        manifest = json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    else:
        record = json.loads((reports/f"{selected['id']}静态测量.json").read_text(encoding='utf-8'))
        root = Path(record['root'])
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        path = root/'project/snapshots'/manifest['id']
    path.resolve(strict=True).relative_to(repository/'output')
    return path,manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id',required=True,choices=['hiredis-c','spdlog-cpp','flask-python','zod-typescript'])
    parser.add_argument('--narrow',action='store_true')
    parser.add_argument('--base-url', default='https://api.deepseek.com', help='核对已授权接收端，不接受密钥参数')
    parser.add_argument('--model', default='deepseek-flash', help='核对实际模型ID')
    parser.add_argument('--json-response', action='store_true', help='仅隔离评测使用非流式JSON，主工作区设置不变')
    parser.add_argument('--reasoning-effort', choices=['default', 'low', 'medium', 'high'],
                        help='仅隔离评测使用指定推理强度，未提供时保留配置')
    parser.add_argument('--max-seconds', type=int, default=0, help='开发评测截止秒数；到时通过任务接口主动暂停，0使用产品原有限预算')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--resume',action='store_true',help='从该样本已有暂停记录和检查点主动恢复，不创建新的快照')
    mode.add_argument('--refresh-overview',action='store_true',help='仅重测已发布结果的概览，架构及源码保持不变')
    arguments = parser.parse_args()
    if not 0 <= arguments.max_seconds <= 86400:
        parser.error('评测截止时间需在0至86400秒之间。')
    sources = json.loads((repository/'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    selected = next(item for item in sources['repositories'] if item['id']==arguments.id)
    snapshot,old_manifest = saved_snapshot(selected)
    destination = reports/f"{arguments.id}-{'narrow' if arguments.narrow else 'full'}真实模型验证.json"
    prior_document = json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else None
    if prior_document and prior_document['状态']=='进行中':
        raise RuntimeError('已有进行中的验证记录，请先核对工作进程与任务状态，不能覆盖。')
    previous = prior_document if arguments.resume or arguments.refresh_overview else None
    if (arguments.resume or arguments.refresh_overview) and previous is None:
        raise RuntimeError('没有可恢复的验证记录。')
    root = Path(previous['数据目录']) if previous else repository/'.runtime/public-model-validation'/str(uuid.uuid4())
    if previous:
        root.resolve(strict=True).relative_to(repository/'.runtime/public-model-validation')
        expected_state = '结构检查通过，待源码质量复核' if arguments.refresh_overview else '分析暂停'
        assert previous['状态']==expected_state and previous['Commit']==selected['commit']
        checkpoint = root/'projects'/previous['project_id']/'architecture-checkpoint.json'
        if checkpoint.exists():
            history = root/'checkpoint-history'
            history.mkdir(exist_ok=True)
            shutil.copyfile(checkpoint,history/(str(uuid.uuid4())+'.json'))
    # 新范围对照或质量重测也必须保留原报告，不能只在恢复暂停任务时归档。
    if prior_document:
        destination.with_name(destination.stem+'-'+str(uuid.uuid4())+'.json').write_text(
            json.dumps(prior_document,ensure_ascii=False,indent=2),encoding='utf-8')
    source = root/'owned-source'
    source.mkdir(parents=True,exist_ok=bool(previous))
    narrow = {'hiredis-c':['read.c','read.h'],'spdlog-cpp':['include/spdlog/logger-inl.h','include/spdlog/logger.h',
        'include/spdlog/common.h','include/spdlog/namespace.h'],
        'flask-python':['src/flask/helpers.py'],'zod-typescript':['packages/zod/src/v4/core/parse.ts']}
    from codescape.snapshots import matches_scope, read_snapshot
    original_files = read_snapshot(snapshot,old_manifest)
    files = {path:text for path,text in original_files.items() if matches_scope(path,selected['initial_includes'])
        and not matches_scope(path,selected['initial_excludes']+sources['common_excludes'])
        and (not arguments.narrow or path in narrow[arguments.id])}
    assert files
    for path in files:
        destination = source/path
        destination.parent.mkdir(parents=True,exist_ok=True)
        if previous:
            assert destination.read_bytes()==(snapshot/path).read_bytes()
        else:
            destination.write_bytes((snapshot/path).read_bytes())
    # Only encrypted bytes are copied; the key is decoded solely inside Provider.
    actual = Path(os.environ['LOCALAPPDATA'])/'CodeScape'
    config = Provider(actual).settings()
    assert config['base_url']==arguments.base_url and config['model']==arguments.model and config['has_key']
    if arguments.json_response:
        config['stream_response'] = False
    if arguments.reasoning_effort is not None:
        config['reasoning_effort'] = arguments.reasoning_effort
    if previous and (previous['接收端'] != config['base_url'] or previous['模型'] != config['model']):
        raise RuntimeError('旧评测接收端或模型不同，不能直接恢复；请新建评测，历史结果已保留。')
    (root/'credentials').mkdir(exist_ok=True)
    shutil.copyfile(actual/'credentials/provider.dpapi',root/'credentials/provider.dpapi')
    (root/'provider.json').write_text(json.dumps({'base_url':config['base_url'],'model':config['model'],
                                               'stream_response':config.get('stream_response',False),
                                               'reasoning_effort':config.get('reasoning_effort','default')}),encoding='utf-8')
    nonce = 'public-model-validation'
    record = {'日期':(datetime.now(UTC) + timedelta(hours=8)).date().isoformat(),
        '检查开始时间UTC':datetime.now(UTC).isoformat(),'样本':arguments.id,'Commit':selected['commit'],
        '范围':'缩小范围' if arguments.narrow else '约定完整范围','授权':'用户允许四个固定公开样本向当前配置接收端上传并评测',
        '接收端':config['base_url'],'模型':config['model'],'流式接收':config.get('stream_response',False),
        '当前请求推理强度':config.get('reasoning_effort','default'),
        '流水线':PIPELINE_VERSION,'数据目录':str(root),
        '原始快照hash':old_manifest['hash'],'实际文件':list(files),'状态':'进行中','执行目标代码':False}
    record['实施代码起始sha256'] = implementation_hash()
    record['开发评测截止秒数'] = arguments.max_seconds or None
    destination = reports/f"{arguments.id}-{'narrow' if arguments.narrow else 'full'}真实模型验证.json"
    if previous:
        record['恢复前已知total_tokens'] = previous.get('已知total_tokens')
    started = time.monotonic()
    def save():
        document = dict(record)
        if document.get('analysis') is not None:
            raw = json.dumps(document.pop('analysis'),ensure_ascii=False,indent=2).encode('utf-8')
            checksum = hashlib.sha256(raw).hexdigest()
            artifacts = root/'validation-artifacts'
            artifacts.mkdir(exist_ok=True)
            artifact = artifacts/(checksum+'.json')
            if artifact.exists():
                assert artifact.read_bytes()==raw
            else:
                artifact.write_bytes(raw)
            document['静态索引制品'] = {'path':str(artifact),'sha256':checksum,'bytes':len(raw)}
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(document,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(destination)
    def wait(client,url,terminal):
        previous = None
        while True:
            value = client.get(url).json()['data']
            progress = value.get('analysis_progress') or {}
            marker = (value['status'],progress.get('phase'),progress.get('completed'),progress.get('total'))
            if marker != previous:
                print(json.dumps({'样本':arguments.id,'范围':record['范围'],'状态':marker},ensure_ascii=False),flush=True)
                previous = marker
                record['进度'] = progress
                record['耗时秒'] = round(time.monotonic()-started,2)
                save()
            if value['status'] in terminal:
                return value
            if (arguments.max_seconds and time.monotonic() - started >= arguments.max_seconds
                    and value['status'] in {'indexing', 'analyzing'}):
                jobs = [job for job in database.list_jobs()
                        if job['project_id'] == value['id'] and job['status'] == 'running']
                if jobs:
                    stopped = client.post(f"/api/v1/tasks/{jobs[0]['id']}/pause", json={})
                    if stopped.status_code == 200:
                        record['开发评测主动暂停'] = '达到评测截止时间，已调用产品暂停接口；在途远端用量可能未知。'
                        save()
                        continue
                    assert stopped.status_code == 409, stopped.status_code
            time.sleep(2)
    save()
    database = Database(root)
    try:
        with TestClient(create_app(root,nonce)) as client:
            client.headers['x-csrf-token'] = client.post('/api/v1/session/exchange',json={'nonce':nonce}).json()['data']['csrf_token']
            if previous:
                project = client.get(f"/api/v1/projects/{previous['project_id']}").json()['data']
                expected_status = 'ready' if arguments.refresh_overview else 'paused'
                assert project['version_id']==previous['version_id'] and project['status']==expected_status
            else:
                imported = client.post('/api/v1/imports',json={'path':str(source)})
                assert imported.status_code == 200,imported.status_code
                project = imported.json()['data']
            url = f"/api/v1/projects/{project['id']}"
            indexed = project if arguments.refresh_overview else wait(client,url,{'indexed','paused'})
            index_paused = indexed['status'] == 'paused' and not previous
            assert indexed['status']=='indexed' or previous or index_paused,indexed.get('error')
            record['project_id'] = project['id']
            record['version_id'] = indexed['version_id']
            row = database.get(project['id'])
            if index_paused:
                record['索引未完成'] = '索引阶段已通过任务接口暂停，本次没有开始AI分析；恢复需先完成静态索引。'
                record['索引暂停时快照文件'] = list(row['snapshot']['files'])
            else:
                assert set(row['snapshot']['files']) == set(files)
            record['实际快照hash'] = row['snapshot']['hash']
            record['scope_hash'] = row['snapshot']['scope_hash']
            if not index_paused:
                consent = client.post(url+'/consent',json={'accepted':True,'version_id':indexed['version_id']})
                assert consent.status_code == 200
            if index_paused:
                result = indexed
            elif arguments.refresh_overview:
                before_modules = indexed['architecture']['modules']
                begun = client.post(url+'/overview',json={})
                record['概览重测状态码'] = begun.status_code
                result = client.get(url).json()['data']
                assert result['architecture']['modules'] == before_modules
                record['模块架构未变'] = True
                if begun.status_code != 200:
                    record['概览重测错误'] = '概览重测未成功，原结果保留；实际服务错误见请求账本。'
            else:
                begun = client.post(url+'/analyze',json={})
                assert begun.status_code == 200
                result = wait(client,url,{'ready','paused'})
            record.update(状态='结构检查通过，待源码质量复核' if result['status']=='ready' else '分析暂停',
                错误=result.get('error'),耗时秒=round(time.monotonic()-started,2),
                analysis=result.get('analysis'),architecture=result.get('architecture'))
            assert original_files == read_snapshot(snapshot,old_manifest)
            record['原快照字节未变'] = True
            checkpoint_path = root/'projects'/record['project_id']/'architecture-checkpoint.json'
            if checkpoint_path.exists():
                from codescape.provider import checkpoint_source_generations
                saved_checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8'))
                record['源码生成版本集合'] = sorted(checkpoint_source_generations(saved_checkpoint))
            else:
                version = database.version(record['project_id'], record['version_id'])
                prepared = (version or {}).get('value', {}).get('prepared_output') or {}
                record['源码生成版本集合'] = prepared.get('source_generation_versions')
            with database.engine.connect() as connection:
                from sqlalchemy import select
                rows = [dict(row) for row in connection.execute(select(database.requests)).mappings()]
            record['请求账本'] = rows
            record['请求状态'] = dict(Counter(row['status'] for row in rows))
            record['已知total_tokens'] = sum((row.get('usage') or {}).get('total_tokens',0) for row in rows)
            record['未知用量请求'] = sum('total_tokens' not in (row.get('usage') or {}) for row in rows)
            record['费用'] = None
            diagnostics = root/'validation-errors.jsonl'
            record['脱敏校验记录'] = [json.loads(line) for line in diagnostics.read_text(encoding='utf-8').splitlines()] if diagnostics.exists() else []
            record['边界'] = '真实配置接口，固定响应与质量复核分开；尚未代表用户验收；价格未知不记零费用。'
            record['实施代码结束sha256'] = implementation_hash()
            record['评测期间实施代码一致'] = record['实施代码起始sha256'] == record['实施代码结束sha256']
            if not record['评测期间实施代码一致']:
                record['边界'] += '评测期间实施代码发生修订，本记录不能计为同一实现版本完整验收。'
            save()
            print(json.dumps({key:record[key] for key in ['样本','范围','状态','请求状态','已知total_tokens','未知用量请求']},ensure_ascii=False),flush=True)
    finally:
        database.engine.dispose()


if __name__=='__main__':
    main()
