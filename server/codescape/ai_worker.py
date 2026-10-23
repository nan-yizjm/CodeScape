"""Application-owned AI process. It never imports or executes analyzed source."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from .checkpoints import dump_checkpoint, load_checkpoint
from .database import Database
from .ledger import ai_scope
from .models import Analysis
from .provider import Provider, ProviderError


def emit(event, **values):
    sys.stdout.write(json.dumps({"event": event, **values}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def run(payload):
    database = Database(Path(payload["root"]))
    provider = Provider(Path(payload["root"]), database)
    async def progress(completed, total, detail):
        emit("progress", completed=completed, total=total, detail=detail)
    async def batch(completed, total, architecture):
        emit("batch", completed=completed, total=total, architecture=dump_checkpoint(architecture))
        # Do not send another paid request before the parent durably saves this unit.
        response = await asyncio.to_thread(sys.stdin.buffer.readline)
        if response != b'{"ack":"batch"}\n':
            raise ProviderError("批次检查点未确认落盘，工作进程已停止。")
    try:
        with ai_scope(project_id=payload["project_id"], version_id=payload["version_id"], job_id=payload["job_id"],
                      request_config=payload["request_config"]):
            result, usage = await provider.architecture(Analysis.model_validate(payload["analysis"]), payload["files"],
                on_progress=progress, on_batch=batch,
                completed_batches=[load_checkpoint(item) for item in payload["completed_batches"]])
        emit("complete", architecture=result.model_dump(), usage=usage)
    finally:
        database.engine.dispose()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        payload = json.loads(sys.stdin.buffer.readline(256 * 1024**2))
        asyncio.run(run(payload))
    except ProviderError as error:
        emit("error", message=str(error)[:300])
        raise SystemExit(1) from None
    except Exception:
        emit("error", message="AI 工作进程未完成安全校验或保存，已有结果保持可读。")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
