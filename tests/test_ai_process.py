import asyncio
import ctypes
import json
import sys
import time
from ctypes import wintypes

import pytest
from codescape.ai_process import run_architecture
from codescape.database import Database
from codescape.ledger import RequestLedger, ai_scope
from codescape.models import Analysis, Architecture, Overview
from codescape.provider import Provider, ProviderError

ARCHITECTURE = Architecture(modules=[], overview=Overview(introduction="应用自有固定进程验证",
    capabilities=[], principle="不访问模型，不执行被分析源码", limitations=["固定响应，不代表模型验收"]))


def worker_adapter(monkeypatch, body):
    """Run the real IPC/worker loop with application-owned fixed Python, never source."""
    original = asyncio.create_subprocess_exec
    code = '''
import asyncio, subprocess, sys
from pathlib import Path
from codescape.ai_worker import main
from codescape.models import Architecture
from codescape.provider import Provider
async def forbidden(*args, **kwargs):
    raise AssertionError("固定进程验证禁止调用模型")
Provider.request = forbidden
''' + body + '\nProvider.architecture = fixed\nmain()\n'
    async def create(*args, **kwargs):
        assert args[-1] == "codescape.ai_worker"
        return await original(sys.executable, "-c", code, **kwargs)
    monkeypatch.setattr("codescape.ai_process.asyncio.create_subprocess_exec", create)


def arguments(tmp_path):
    database = Database(tmp_path)
    provider = Provider(tmp_path, database)
    return database, provider, {"project_id": "test-project", "version_id": "test-version", "job_id": "test-job",
        "completed_batches": []}


def test_real_worker_waits_for_durable_checkpoint_before_continuing(tmp_path, monkeypatch):
    document = json.dumps(ARCHITECTURE.model_dump(), ensure_ascii=False)
    worker_adapter(monkeypatch, f'''
async def fixed(self, analysis, files, *, on_progress, on_batch, completed_batches):
    assert files == {{"owned.py": "raise RuntimeError('这个源码只能读取')"}}
    value = Architecture.model_validate_json({document!r})
    await on_progress(0, 1, "等待检查点")
    await on_batch(1, 1, value)
    assert (self.root / "durable-checkpoint.json").exists()
    await on_progress(1, 1, "已确认检查点")
    return value, {{"total_tokens": 0}}
''')
    async def scenario():
        database, provider, options = arguments(tmp_path)
        events = []
        async def progress(completed, total, detail):
            events.append(detail)
        async def batch(completed, total, architecture):
            assert (completed, total) == (1, 1)
            (tmp_path / "durable-checkpoint.json").write_text(architecture.model_dump_json(), encoding="utf-8")
        result, usage = await run_architecture(provider, Analysis(), {"owned.py": "raise RuntimeError('这个源码只能读取')"},
            on_progress=progress, on_batch=batch, **options)
        assert result == ARCHITECTURE and usage == {"total_tokens": 0}
        assert events == ["等待检查点", "已确认检查点"]
        assert RequestLedger(database).summary()["request_count"] == 0
        database.engine.dispose()
    asyncio.run(scenario())


def test_cancel_kills_worker_descendants_and_recovers_only_its_unknown_receipt(tmp_path, monkeypatch):
    worker_adapter(monkeypatch, '''
async def fixed(self, analysis, files, *, on_progress, **kwargs):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
    (self.root / "worker-pids.json").write_text(__import__("json").dumps([__import__("os").getpid(), child.pid]))
    await on_progress(0, 1, "已启动应用自有进程树")
    await asyncio.Event().wait()
''')
    async def scenario():
        database, provider, options = arguments(tmp_path)
        ledger, ready = RequestLedger(database), asyncio.Event()
        with ai_scope(job_id=options["job_id"]):
            sent = ledger.prepare({"base_url": "https://example.invalid", "model": "固定响应"}, {}, "unit", 1)
            ledger.sent(sent)
        with ai_scope(job_id="other-job"):
            other = ledger.prepare({"base_url": "https://example.invalid", "model": "固定响应"}, {}, "unit", 1)
            ledger.sent(other)
        async def progress(*args):
            ready.set()
        async def batch(*args):
            raise AssertionError("没有完成批次")
        task = asyncio.create_task(run_architecture(provider, Analysis(), {}, on_progress=progress, on_batch=batch, **options))
        await asyncio.wait_for(ready.wait(), 10)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = [kernel.OpenProcess(0x100000, False, pid) for pid in json.loads((tmp_path / "worker-pids.json").read_text())]
        try:
            assert all(handles)
            started = time.monotonic()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)
            assert time.monotonic() - started < 10
            assert all(kernel.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
            records = {item["id"]: item for item in ledger.summary()["records"]}
            assert records[sent]["status"] == "outcome_unknown"
            assert records[other]["status"] == "sent"
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            for handle in handles:
                if handle:
                    kernel.CloseHandle(handle)
            database.engine.dispose()
    asyncio.run(scenario())


def test_actual_worker_without_credentials_fails_without_upload(tmp_path):
    async def scenario():
        database, provider, options = arguments(tmp_path)
        async def callback(*args):
            pass
        with pytest.raises(ProviderError, match="配置 API Key"):
            await run_architecture(provider, Analysis(), {"owned.py": "def sample(): pass"},
                on_progress=callback, on_batch=callback, **options)
        assert RequestLedger(database).summary()["request_count"] == 0
        database.engine.dispose()
    asyncio.run(scenario())
