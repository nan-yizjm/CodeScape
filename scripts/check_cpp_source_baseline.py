"""固定公开快照的人工源码基线；只执行 CodeScape 静态工作进程。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

from codescape.models import Analysis
from codescape.runtime import worker_command
from codescape.workers import WorkerJob

repository = Path(__file__).resolve().parents[1]
reports = repository/'docs/reports/p0'


def owned_path(path):
    path = Path(path).resolve(strict=True)
    path.relative_to(repository/'output')
    return path


def check_calls(analysis, root, cases):
    entities = {entity.id:entity for entity in analysis.entities}
    scopes = {scope.id:scope for scope in analysis.scopes}
    checked = []
    for path,line,caller,expression,target,resolution in cases:
        matches = [call for call in analysis.calls if call.location.path == path and call.location.start_line == line
            and call.expression == expression and scopes[call.caller_scope_id].name == caller]
        assert len(matches) == 1,(path,line,caller,expression)
        call = matches[0]
        assert call.resolution == resolution,(path,line,call.resolution)
        implementation = entities.get(call.target_id)
        assert (implementation.qualified_name if implementation else None) == target
        source = (root/path).read_bytes()
        assert expression in source.splitlines()[line-1].decode()
        assert call.location.start_byte < call.location.end_byte <= len(source)
        checked.append({'文件':path,'行':line,'真实作用域':caller,'表达式':expression,'预期目标':target,
            '结果':resolution,'证据字节':source[call.location.start_byte:call.location.end_byte].decode(),
            '目标位置':implementation.location.model_dump() if implementation else None})
    return checked


def main():
    bundle = json.loads((reports/'Windows独立运行验证.json').read_text(encoding='utf-8'))
    hiredis = owned_path(bundle['data_root'])
    with sqlite3.connect((hiredis/'app.db').as_uri()+'?mode=ro',uri=True) as connection:
        rows = connection.execute('select snapshot,result from prototype_projects').fetchall()
    manifest,old = next((json.loads(snapshot),json.loads(result)) for snapshot,result in rows
        if result and 'hiredis.c' in json.loads(snapshot)['files'])
    assert manifest['origin']['commit'] == '058ebcdf36c5b05b759c613564fb958ec363c90f'
    hroot = next((hiredis/'projects').glob(f'*/snapshots/{manifest["id"]}'))
    c_cases = [('hiredis.c',708,'redisReaderCreate','redisReaderCreateWithFunctions','redisReaderCreateWithFunctions','confirmed'),
        ('hiredis.c',713,'redisPushAutoFree','freeReplyObject','freeReplyObject','confirmed'),
        ('hiredis.c',953,'redisSetTimeout','redisContextSetTimeout','redisContextSetTimeout','confirmed'),
        ('hiredis.c',958,'redisEnableKeepAliveWithInterval','redisKeepAlive','redisKeepAlive','confirmed'),
        ('hiredis.c',963,'redisEnableKeepAlive','redisKeepAlive','redisKeepAlive','confirmed'),
        ('hiredis.c',968,'redisSetTcpUserTimeout','redisContextSetTcpUserTimeout','redisContextSetTcpUserTimeout','confirmed'),
        ('hiredis.c',742,'redisFree','c->funcs->close',None,'excluded_indirect'),
        ('alloc.c',67,'hi_malloc','hiredisAllocFns.mallocFn',None,'excluded_indirect')]
    c = check_calls(Analysis.model_validate(old),hroot,c_cases)
    measured = json.loads((reports/'spdlog-cpp带依赖静态测量.json').read_text(encoding='utf-8'))
    spdlog = owned_path(measured['root'])
    sroot = next((spdlog/'project/snapshots').iterdir())
    manifest = json.loads((sroot/'manifest.json').read_text(encoding='utf-8'))
    hashes = {path:hashlib.sha256((sroot/path).read_bytes()).hexdigest() for path in manifest['files']}
    started = time.perf_counter()
    process = subprocess.Popen(worker_command('codescape.index_worker','--index-worker'),stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,cwd=repository,creationflags=subprocess.CREATE_NO_WINDOW)
    job = WorkerJob()
    try:
        job.attach(process.pid)
        output,_ = process.communicate(json.dumps({'root':str(sroot),'manifest':manifest,
            'version':'representative-index'}).encode(),timeout=300)
        assert process.returncode == 0
        analysis = Analysis.model_validate_json(output)
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait()
    assert hashes == {path:hashlib.sha256((sroot/path).read_bytes()).hexdigest() for path in manifest['files']}
    path = 'include/spdlog/logger-inl.h'
    expected = {98:'spdlog::logger::flush',102:'spdlog::logger::flush_level',
        107:'spdlog::logger::sinks',109:'spdlog::logger::sinks',166:'spdlog::logger::should_flush_'}
    definitions = []
    for line,name in expected.items():
        entities = [entity for entity in analysis.entities if entity.location.path == path and entity.location.start_line == line]
        assert len(entities) == 1,(line,[entity.qualified_name for entity in entities])
        entity = entities[0]
        assert entity.qualified_name == name,(line,entity.qualified_name)
        definitions.append({'文件':path,'行':line,'限定名':name,'签名':entity.signature,'ID':entity.id})
    assert definitions[2]['ID'] != definitions[3]['ID'] and definitions[2]['签名'] != definitions[3]['签名']
    cpp = check_calls(analysis,sroot,[(path,167,'spdlog::logger::should_flush_','flush_level',
        'spdlog::logger::flush_level','confirmed'),(path,98,'spdlog::logger::flush','flush_',None,'excluded_indirect')])
    (spdlog/'static-analysis-cpp-config6.json').write_text(analysis.model_dump_json(indent=2),encoding='utf-8')
    report = {'日期':'2026-10-11','基线性质':'开发代理逐条阅读固定源码后写入预期，不从解析结果生成预期',
        'HiredisCommit':bundle['public_git_import']['origin']['commit'],'spdlogCommit':measured['origin']['commit'],
        'C调用':c,'C++定义':definitions,'C++调用':cpp,'C++重载保留':True,'源码字节未变':True,
        '本轮完整C++耗时秒':round(time.perf_counter()-started,3),'工具':analysis.tools,
        '模型请求':0,'目标代码执行':False,'边界':'仅抽样 8 项 C 调用、5 项 C++ 定义和 2 项 C++ 调用；不外推工程整体准确率、全部配置覆盖或 AI 架构质量。'}
    (reports/'C与C++源码抽样基线.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'C调用':len(c),'C++定义':len(definitions),'C++调用':len(cpp),
        '完整C++耗时秒':report['本轮完整C++耗时秒'],'模型请求':0},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
