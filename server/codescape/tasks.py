from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

from .database import Database, now

ACTIVE_STATES = {"waiting", "running", "paused"}
Worker = Callable[[], Awaitable[None]]


class TaskQueue:
    """A durable queue with one explicit start; completion never consumes its successor."""

    def __init__(self, database: Database, lock: asyncio.Lock):
        self.database = database
        self.lock = lock
        self.running: dict[str, asyncio.Task] = {}
        self.stop_requests: dict[str, str] = {}

    def create(self, project: dict, kind: str) -> dict:
        existing = [job for job in self.database.list_jobs()
                    if job["project_id"] == project["id"] and job["status"] in ACTIVE_STATES]
        if existing:
            if existing[0]["kind"] != kind:
                raise ValueError("当前项目已有未结束任务，请先完成或取消。")
            return existing[0]
        timestamp = now()
        value = {"id": str(uuid.uuid4()), "project_id": project["id"], "kind": kind,
                 "status": "waiting", "position": len(self.database.list_jobs()),
                 "version_id": project["version_id"], "created_at": timestamp,
                 "updated_at": timestamp, "detail": "等待主动启动"}
        self.database.insert_job(value)
        return value

    async def start(self, job_id: str, worker: Worker) -> dict:
        job = self.database.get_job(job_id)
        if not job or job["status"] not in {"waiting", "paused"}:
            raise ValueError("此任务不能启动，请刷新任务状态。")
        if self.lock.locked():
            raise ValueError("已有任务运行；本任务已保留，请空闲后主动启动。")
        # No await between checking and acquiring an unlocked asyncio lock.
        await self.lock.acquire()
        self.database.update_job(job_id, status="running", detail="任务正在运行")
        started = asyncio.Event()

        async def run():
            try:
                started.set()
                await worker()
                project = self.database.get(job["project_id"])
                status = ("completed" if project and project["status"] in {"ready", "indexed"}
                          else "failed" if project and project["status"] == "failed" else "paused")
                self.database.update_job(job_id, status=status,
                                         detail="任务已完成" if status == "completed" else "任务已暂停，请检查原因")
            except asyncio.CancelledError:
                requested = self.stop_requests.pop(job_id, "paused")
                self.database.update_job(job_id, status=requested,
                                         detail="任务已取消" if requested == "cancelled" else "任务已暂停，可主动恢复")
                raise
            except Exception:
                self.database.update_job(job_id, status="failed", detail="任务失败，请检查项目诊断")
                raise
            finally:
                self.running.pop(job_id, None)
                self.lock.release()

        task = asyncio.create_task(run())
        self.running[job_id] = task
        # The worker records safe diagnostics; always retrieve failures to avoid
        # unhandled-task logs containing exception payloads.
        task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        await started.wait()
        return self.database.get_job(job_id) or job

    async def stop(self, job_id: str, cancel: bool = False) -> dict:
        job = self.database.get_job(job_id)
        if not job or job["status"] not in ACTIVE_STATES:
            raise ValueError("此任务已经结束，请刷新任务状态。")
        desired = "cancelled" if cancel else "paused"
        task = self.running.get(job_id)
        if task:
            self.stop_requests[job_id] = desired
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            self.database.update_job(job_id, status=desired,
                                     detail="任务已取消" if cancel else "任务已暂停，可主动恢复")
        return self.database.get_job(job_id) or job

    async def shutdown(self) -> None:
        for job_id in list(self.running):
            await self.stop(job_id)
