import asyncio

import pytest
from codescape.database import Database
from codescape.tasks import TaskQueue


def add_project(database, name):
    project = {"id": name, "name": name, "source": "fixture", "created_at": "now",
               "status": "indexed", "snapshot": {"files": {}}, "version_id": "v", "demo": 0}
    database.insert(project)
    return project


def test_queue_is_manual_durable_and_ordered(tmp_path):
    async def scenario():
        db = Database(tmp_path)
        queue = TaskQueue(db, asyncio.Lock())
        a, b, c = [queue.create(add_project(db, name), "architecture") for name in ["a", "b", "c"]]
        entered, release = asyncio.Event(), asyncio.Event()
        runs = []

        async def worker():
            runs.append("a")
            entered.set()
            await release.wait()
            db.update("a", status="ready")

        await queue.start(a["id"], worker)
        await entered.wait()
        with pytest.raises(ValueError, match="已有任务"):
            await queue.start(b["id"], worker)
        assert sum(job["status"] == "running" for job in db.list_jobs()) == 1
        db.reorder_jobs([c["id"], b["id"]])
        with pytest.raises(ValueError, match="等待队列"):
            db.reorder_jobs([b["id"]])
        running = queue.running[a["id"]]
        release.set()
        await running
        await asyncio.sleep(0)
        assert runs == ["a"]
        assert [job["id"] for job in db.list_jobs() if job["status"] == "waiting"] == [c["id"], b["id"]]
        assert db.get_job(a["id"])["status"] == "completed"
        assert not queue.lock.locked()
        db.engine.dispose()
        restarted = Database(tmp_path)
        restarted.recover_interrupted()
        assert len([job for job in restarted.list_jobs() if job["status"] == "waiting"]) == 2
        assert queue.running == {}
        restarted.engine.dispose()

    asyncio.run(scenario())


def test_pause_cancel_and_immediate_stop_release_slot(tmp_path):
    async def scenario():
        db = Database(tmp_path)
        queue = TaskQueue(db, asyncio.Lock())
        project = add_project(db, "p")
        job = queue.create(project, "architecture")
        calls = []

        async def worker():
            calls.append("request")
            await asyncio.Event().wait()

        await queue.start(job["id"], worker)
        await queue.stop(job["id"])
        assert db.get_job(job["id"])["status"] == "paused"
        assert not queue.lock.locked()
        await queue.start(job["id"], worker)
        await queue.stop(job["id"], cancel=True)
        assert db.get_job(job["id"])["status"] == "cancelled"
        assert not queue.running and not queue.lock.locked()
        assert calls == ["request", "request"]
        with pytest.raises(ValueError, match="不能启动"):
            await queue.start(job["id"], worker)
        replacement = queue.create(project, "architecture")
        assert replacement["id"] != job["id"]
        await queue.stop(replacement["id"], cancel=True)
        assert db.get_job(replacement["id"])["status"] == "cancelled"
        db.engine.dispose()

    asyncio.run(scenario())


def test_shutdown_and_crash_recovery_do_not_run_waiting_jobs(tmp_path):
    async def scenario():
        db = Database(tmp_path)
        queue = TaskQueue(db, asyncio.Lock())
        a, b = [queue.create(add_project(db, name), "index") for name in ["a", "b"]]

        async def worker():
            db.update("a", status="indexing")
            await asyncio.Event().wait()

        await queue.start(a["id"], worker)
        await queue.shutdown()
        assert db.get_job(a["id"])["status"] == "paused"
        assert db.get_job(b["id"])["status"] == "waiting"
        # Emulate a process exit before the worker's cancellation handler ran.
        db.update_job(a["id"], status="running")
        db.engine.dispose()
        restarted = Database(tmp_path)
        restarted.recover_interrupted()
        assert restarted.get_job(a["id"])["status"] == "paused"
        assert restarted.get_job(b["id"])["status"] == "waiting"
        restarted.engine.dispose()

    asyncio.run(scenario())


def test_shutdown_has_one_deadline_and_persists_stop_before_cleanup(tmp_path):
    async def scenario():
        db = Database(tmp_path)
        queue = TaskQueue(db, asyncio.Lock())
        a, b = [queue.create(add_project(db, name), "architecture") for name in ("a", "b")]
        cleaning = asyncio.Event()
        async def worker():
            try:
                await asyncio.Event().wait()
            finally:
                assert db.get_job(a["id"])["status"] == "paused"
                cleaning.set()
                await asyncio.Event().wait()
        await queue.start(a["id"], worker)
        task = queue.running[a["id"]]
        started = asyncio.get_running_loop().time()
        await queue.shutdown(timeout=0.1)
        assert asyncio.get_running_loop().time() - started < 0.5
        assert cleaning.is_set() and db.get_job(a["id"])["status"] == "paused"
        assert db.get_job(b["id"])["status"] == "waiting"
        with pytest.raises(ValueError, match="正在退出"):
            queue.create(db.get("a"), "architecture")
        with pytest.raises(ValueError, match="正在退出"):
            await queue.start(b["id"], worker)
        await asyncio.gather(task, return_exceptions=True)
        assert not queue.running and not queue.lock.locked()
        db.engine.dispose()
    asyncio.run(scenario())
