"""已授权的四个固定公开源码样本：运行产品导入、分析与账本路径，不执行目标代码。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

from codescape.app import create_app
from codescape.database import Database
from codescape.provider import PIPELINE_VERSION, Provider
from fastapi.testclient import TestClient

repository = Path(__file__).resolve().parents[1]
reports = repository/'docs/reports/p0'


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
    parser.add_argument('--resume',action='store_true',help='从该样本已有暂停记录和检查点主动恢复，不创建新的快照')
    arguments = parser.parse_args()
    sources = json.loads((repository/'docs/benchmark_sources.json').read_text(encoding='utf-8'))
    selected = next(item for item in sources['repositories'] if item['id']==arguments.id)
    snapshot,old_manifest = saved_snapshot(selected)
    destination = reports/f"{arguments.id}-{'narrow' if arguments.narrow else 'full'}真实模型验证.json"
    previous = json.loads(destination.read_text(encoding='utf-8')) if arguments.resume else None
    root = Path(previous['数据目录']) if previous else repository/'.runtime/public-model-validation'/str(uuid.uuid4())
    if previous:
        root.resolve(strict=True).relative_to(repository/'.runtime/public-model-validation')
        assert previous['状态']=='分析暂停' and previous['Commit']==selected['commit']
        destination.with_name(destination.stem+'-'+str(uuid.uuid4())+'.json').write_text(
            json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
    source = root/'owned-source'
    source.mkdir(parents=True,exist_ok=bool(previous))
    narrow = {'hiredis-c':['read.c','read.h'],'spdlog-cpp':['include/spdlog/logger-inl.h','include/spdlog/logger.h'],
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
    assert config['base_url']=='https://api.deepseek.com' and config['model']=='deepseek-flash' and config['has_key']
    if not previous:
        (root/'credentials').mkdir()
        shutil.copyfile(actual/'credentials/provider.dpapi',root/'credentials/provider.dpapi')
        (root/'provider.json').write_text(json.dumps({'base_url':config['base_url'],'model':config['model']}),encoding='utf-8')
    nonce = 'public-model-validation'
    record = {'日期':'2026-10-11','样本':arguments.id,'Commit':selected['commit'],
        '范围':'缩小范围' if arguments.narrow else '约定完整范围','授权':'用户允许四个固定公开样本真实评测',
        '接收端':config['base_url'],'模型':config['model'],'流水线':PIPELINE_VERSION,'数据目录':str(root),
        '原始快照hash':old_manifest['hash'],'实际文件':list(files),'状态':'进行中','执行目标代码':False}
    destination = reports/f"{arguments.id}-{'narrow' if arguments.narrow else 'full'}真实模型验证.json"
    if previous:
        record['恢复前已知total_tokens'] = previous.get('已知total_tokens')
    started = time.monotonic()
    def save():
        destination.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
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
            time.sleep(2)
    save()
    database = Database(root)
    try:
        with TestClient(create_app(root,nonce)) as client:
            client.headers['x-csrf-token'] = client.post('/api/v1/session/exchange',json={'nonce':nonce}).json()['data']['csrf_token']
            if previous:
                project = client.get(f"/api/v1/projects/{previous['project_id']}").json()['data']
                assert project['version_id']==previous['version_id'] and project['status']=='paused'
            else:
                imported = client.post('/api/v1/imports',json={'path':str(source)})
                assert imported.status_code == 200,imported.status_code
                project = imported.json()['data']
            url = f"/api/v1/projects/{project['id']}"
            indexed = wait(client,url,{'indexed','paused'})
            assert indexed['status']=='indexed' or previous,indexed.get('error')
            record['project_id'] = project['id']
            record['version_id'] = indexed['version_id']
            row = database.get(project['id'])
            assert set(row['snapshot']['files']) == set(files)
            record['实际快照hash'] = row['snapshot']['hash']
            record['scope_hash'] = row['snapshot']['scope_hash']
            consent = client.post(url+'/consent',json={'accepted':True,'version_id':indexed['version_id']})
            assert consent.status_code == 200
            begun = client.post(url+'/analyze',json={})
            assert begun.status_code == 200
            result = wait(client,url,{'ready','paused'})
            record.update(状态='结构检查通过，待源码质量复核' if result['status']=='ready' else '分析暂停',
                错误=result.get('error'),耗时秒=round(time.monotonic()-started,2),
                analysis=result.get('analysis'),architecture=result.get('architecture'))
            assert original_files == read_snapshot(snapshot,old_manifest)
            record['原快照字节未变'] = True
            with database.engine.connect() as connection:
                from sqlalchemy import select
                rows = [dict(row) for row in connection.execute(select(database.requests)).mappings()]
            record['请求账本'] = rows
            record['请求状态'] = dict(Counter(row['status'] for row in rows))
            record['已知total_tokens'] = sum((row.get('usage') or {}).get('total_tokens',0) for row in rows)
            record['未知用量请求'] = sum(row.get('usage') is None for row in rows)
            record['费用'] = None
            record['边界'] = '真实官方模型，固定响应与质量复核分开；尚未代表用户验收；价格未知不记零费用。'
            save()
            print(json.dumps({key:record[key] for key in ['样本','范围','状态','请求状态','已知total_tokens','未知用量请求']},ensure_ascii=False),flush=True)
    finally:
        database.engine.dispose()


if __name__=='__main__':
    main()
