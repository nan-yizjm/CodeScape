"""静态工作进程边界；原生故障后保留完整语法索引，不执行目标代码。"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from .models import Analysis, Diagnostic
from .runtime import resource_root, worker_command
from .workers import WorkerJob


async def _invoke(root,manifest,version,mode,timeout):
    process,job = None,None
    try:
        spawning=asyncio.create_task(asyncio.create_subprocess_exec(
            *worker_command('codescape.index_worker','--index-worker'),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            cwd=resource_root(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)))
        try:
            process=await asyncio.shield(spawning)
        except asyncio.CancelledError:
            process=await spawning
            raise
        job=WorkerJob()
        job.attach(process.pid)
        payload=json.dumps({'root':str(root),'manifest':manifest,'version':version,'mode':mode}).encode('utf-8')
        output,_=await asyncio.wait_for(process.communicate(payload),timeout=timeout)
        if process.returncode:
            raise ValueError('静态工作进程未完成。')
        return Analysis.model_validate_json(output)
    finally:
        if job:
            job.close()
        if process and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


async def index_snapshot(root,manifest,version,timeout=120):
    try:
        return await _invoke(root,manifest,version,'full',timeout)
    except (TimeoutError,OSError,ValueError):
        if not any(Path(path).suffix in {'.c','.h','.cpp','.hpp','.cc','.cxx','.hh'} for path in manifest['files']):
            raise ValueError('静态解析进程未完成，原始源码与旧结果未被修改。') from None
    # A second owned worker bypasses native C/C++ semantics. It retains all syntax
    # entities and real scopes, while Python/TS still use their existing analyzers.
    result=await _invoke(root,manifest,version,'syntax_fallback',120)
    result.diagnostics.append(Diagnostic(category='native_index_incomplete',message=
        '完整静态工作进程未完成，已保留完整 C/C++ 语法索引及可验证的其他语言结果；本轮 C/C++ 调用未做原生语义确认。可缩小范围或补充配置后新建快照重试。'))
    return result
