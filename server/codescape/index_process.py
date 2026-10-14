"""静态工作进程边界；原生故障后保留完整语法索引，不执行目标代码。"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from .models import Analysis, Diagnostic
from .runtime import resource_root, worker_command
from .workers import WorkerJob


class IndexProcessError(ValueError):
    """只暴露已观察到的进程边界，不包含原始异常、stderr或解析输入。"""

    messages = {
        'timeout': '达到本次时间上限。',
        'process_exit': '以非零退出码结束。',
        'invalid_output': '输出未通过索引结构校验。',
        'spawn_failed': '无法启动。',
        'isolation_failed': '资源隔离建立失败，已停止解析。',
        'io_failed': '通信失败。',
        'unknown_failure': '校验或处理失败，具体原因未确认。',
    }

    def __init__(self, reason, returncode=None, mode='full'):
        self.reason = reason if reason in self.messages else 'unknown_failure'
        self.returncode = returncode if type(returncode) is int else None
        self.mode = mode if mode in {'full', 'syntax_fallback'} else 'unknown'
        text = {'full': '完整静态工作进程', 'syntax_fallback': '语法回退工作进程',
                'unknown': '静态工作进程'}[self.mode] + self.messages[self.reason]
        if self.reason == 'process_exit' and self.returncode is not None:
            text += f'退出码：{self.returncode}；未据此推断崩溃或内存不足。'
        super().__init__(text)


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
            try:
                process=await spawning
            except Exception:
                pass
            raise
        except OSError:
            raise IndexProcessError('spawn_failed', mode=mode) from None
        try:
            job=WorkerJob()
            job.attach(process.pid)
        except (OSError, RuntimeError):
            raise IndexProcessError('isolation_failed', mode=mode) from None
        payload=json.dumps({'root':str(root),'manifest':manifest,'version':version,'mode':mode}).encode('utf-8')
        try:
            output,_=await asyncio.wait_for(process.communicate(payload),timeout=timeout)
        except TimeoutError:
            raise IndexProcessError('timeout', mode=mode) from None
        except OSError:
            raise IndexProcessError('io_failed', mode=mode) from None
        if process.returncode:
            raise IndexProcessError('process_exit', process.returncode, mode=mode)
        try:
            return Analysis.model_validate_json(output)
        except ValueError:
            raise IndexProcessError('invalid_output', mode=mode) from None
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
    except (TimeoutError,OSError,ValueError) as error:
        failure = error if isinstance(error, IndexProcessError) else IndexProcessError(
            'timeout' if isinstance(error, TimeoutError) else 'unknown_failure')
        if failure.reason == 'isolation_failed':
            raise failure from None
        if not any(Path(path).suffix in {'.c','.h','.cpp','.hpp','.cc','.cxx','.hh'} for path in manifest['files']):
            raise ValueError(str(failure) + '原始源码与旧结果未被修改。') from None
    # A second owned worker bypasses native C/C++ semantics. It retains all syntax
    # entities and real scopes, while Python/TS still use their existing analyzers.
    result=await _invoke(root,manifest,version,'syntax_fallback',120)
    result.diagnostics.append(Diagnostic(category='native_index_' + failure.reason, message=str(failure)))
    result.diagnostics.append(Diagnostic(category='native_index_incomplete',message=
        '完整静态工作进程未完成，已保留完整 C/C++ 语法索引及可验证的其他语言结果；本轮 C/C++ 调用未做原生语义确认。可缩小范围或补充配置后新建快照重试。'))
    return result
