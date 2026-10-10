from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import uuid
from pathlib import Path


def remove_owned_project(root: Path, project_id: str, stopping=None):
    if str(uuid.UUID(project_id)) != project_id:
        raise ValueError("项目目录标识不合法。")
    boundary = root / "projects"
    if (boundary.is_symlink() or boundary.is_junction()
            or not boundary.resolve().is_relative_to(root.resolve())):
        raise ValueError("项目数据目录越界。")
    target = boundary / project_id

    def remove(path: Path):
        if stopping and stopping.is_set():
            raise InterruptedError("清理已暂停")
        try:
            attributes = path.lstat()
        except FileNotFoundError:
            return
        # Junctions and symlinks are removed as links, never traversed/chmod'ed.
        linked = path.is_symlink() or bool(getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        if linked:
            if stat.S_ISDIR(attributes.st_mode):
                path.rmdir()
            else:
                path.unlink()
            return
        if not path.resolve().is_relative_to(target.absolute()):
            raise ValueError("清理目录越界。")
        if stat.S_ISDIR(attributes.st_mode):
            for child in path.iterdir():
                remove(child)
            path.rmdir()
        else:
            try:
                path.unlink()
            except PermissionError:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                path.unlink()
    remove(target)


class ProjectDeletion:
    def __init__(self, root, database, queue, reads):
        self.root, self.database, self.queue, self.reads = root, database, queue, reads
        self.running: dict[str, asyncio.Task] = {}
        self.stopping: dict[str, threading.Event] = {}

    def start(self, project_id: str):
        operation = self.database.begin_delete(project_id)
        if operation["status"] == "completed" or operation["id"] in self.running:
            return operation
        self.reads.revoke(project_id)
        for job in self.database.list_jobs():
            if job["project_id"] == project_id and job["id"] in self.queue.running:
                self.queue.stop_requests[job["id"]] = "cancelled"
                self.queue.running[job["id"]].cancel()
        self.database.update(project_id, lifecycle="deleting")
        self.database.update_operation(operation["id"], status="running", detail="正在停止任务并清理软件内项目数据")
        task = asyncio.create_task(self._run(operation))
        self.stopping[operation["id"]] = threading.Event()
        self.running[operation["id"]] = task
        task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
        return self.database.operation(operation["id"])

    async def _run(self, operation):
        project_id, operation_id = operation["project_id"], operation["id"]
        try:
            for job in self.database.list_jobs():
                if job["project_id"] == project_id and job["status"] in {"waiting", "running", "paused"}:
                    stopping = asyncio.create_task(self.queue.stop(job["id"], cancel=True))
                    stopping.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
                    done, _ = await asyncio.wait([stopping], timeout=30)
                    if not done:
                        raise TimeoutError("任务尚未停止")
                    stopping.result()
            self.database.update_operation(operation_id, phase="reads", detail="等待已经开始的源码读取完成")
            deadline = asyncio.get_running_loop().time() + 30
            while self.reads.project_busy(project_id):
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("源码读取尚未释放")
                await asyncio.sleep(.05)
            self.database.update_operation(operation_id, phase="files", detail="正在清理软件保存的快照和分析数据")
            removal = asyncio.create_task(asyncio.to_thread(remove_owned_project, self.root, project_id,
                                                          self.stopping[operation_id]))
            try:
                await asyncio.shield(removal)
            except asyncio.CancelledError:
                self.stopping[operation_id].set()
                await asyncio.gather(removal, return_exceptions=True)
                raise
            self._remove_consent(project_id)
            self.database.finish_delete(operation_id, project_id)
        except asyncio.CancelledError:
            self.database.update_operation(operation_id, status="pending", detail="清理中断，将在下次启动继续")
            raise
        except Exception:
            self.database.update(project_id, lifecycle="delete_failed")
            self.database.update_operation(operation_id, status="failed", detail="删除未完成：任务、读取或文件尚未释放，或目录校验失败。可以重试。")
        finally:
            self.running.pop(operation_id, None)
            self.stopping.pop(operation_id, None)

    def _remove_consent(self, project_id):
        path = self.root / "consents.json"
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("授权记录需要修复")
        data = {key: value for key, value in data.items() if key != project_id and not key.startswith(project_id + ":")}
        temporary = path.with_suffix(".delete.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)

    async def shutdown(self):
        tasks = list(self.running.values())
        for event in self.stopping.values():
            event.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
