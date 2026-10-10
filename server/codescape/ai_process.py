from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from .analyzers import REPOSITORY
from .ledger import RequestLedger
from .models import Architecture
from .provider import ProviderError
from .workers import WorkerJob


async def run_architecture(provider, analysis, files, *, project_id, version_id, job_id,
                           on_progress, on_batch, completed_batches):
    command = [sys.executable, "--ai-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "codescape.ai_worker"]
    process, container = None, None
    try:
        spawning = asyncio.create_task(asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, cwd=REPOSITORY,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), limit=32 * 1024**2))
        try:
            process = await asyncio.shield(spawning)
        except asyncio.CancelledError:
            # Take ownership of the process even if cancellation races creation.
            process = await spawning
            raise
        container = WorkerJob()
        container.attach(process.pid)
        payload = {"root": str(provider.root), "project_id": project_id, "version_id": version_id, "job_id": job_id,
                   "analysis": analysis.model_dump(), "files": files,
                   "request_config": {key: provider.settings()[key] for key in ("base_url", "model")},
                   "completed_batches": [item.model_dump() for item in completed_batches]}
        process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await process.stdin.drain()
        result = None
        while line := await process.stdout.readline():
            event = json.loads(line)
            if event["event"] == "progress":
                await on_progress(event["completed"], event["total"], event["detail"])
            elif event["event"] == "batch":
                await on_batch(event["completed"], event["total"], Architecture.model_validate(event["architecture"]))
                process.stdin.write(b'{"ack":"batch"}\n')
                await process.stdin.drain()
            elif event["event"] == "complete":
                result = Architecture.model_validate(event["architecture"]), event["usage"]
            elif event["event"] == "error":
                raise ProviderError(event["message"])
            else:
                raise ProviderError("AI 工作进程返回未知事件，已停止。")
        await asyncio.wait_for(process.wait(), timeout=5)
        if process.returncode or result is None:
            raise ProviderError("AI 工作进程中断，已完成的检查点保留。")
        return result
    finally:
        if container:
            container.close()
        if process and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(process.wait(), timeout=5)
        if provider.database:
            RequestLedger(provider.database).recover(job_id=job_id)
