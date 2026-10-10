"""真实 Windows 服务/工作进程退出与检查点恢复；仅应用自有固定响应，无模型请求。"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from codescape.database import Database
from codescape.ledger import RequestLedger

sys.stdout.reconfigure(encoding="utf-8")
repository = Path(__file__).resolve().parents[1]
root = repository / "output/analysis-lifecycle-validation" / str(uuid.uuid4())
root.mkdir(parents=True)
source = root / "owned-source"
source.mkdir()
(source / "owned.py").write_text("def original():\n    return 1\n", encoding="utf-8", newline="")
environment = dict(os.environ, CODESCAPE_DATA_DIR=str(root), PYTHONIOENCODING="utf-8", PYTHONPATH=str(repository / "server"))
worker = '''
import asyncio, os
from codescape.ai_worker import main
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
async def forbidden(*args, **kwargs):
    raise AssertionError("固定进程验证禁止调用模型")
Provider.request = forbidden
async def fixed(self, analysis, files, *, on_progress, on_batch, completed_batches):
    assert files == {"owned.py": "def original():\\n    return 1\\n"}
    value = Architecture(modules=[Module(id="owned-module", name="固定响应验证", summary="读取应用自有样例，不代表模型分析。",
        entity_ids=[entity.id for entity in analysis.entities if entity.visible], code_paths=["owned.py"])],
        overview=Overview(introduction="固定响应退出验证", capabilities=["验证快照恢复"], principle="不执行被分析源码",
            limitations=["固定响应，不代表真实模型质量"]))
    (self.root / "worker-pid.txt").write_text(str(os.getpid()))
    if not completed_batches:
        await on_batch(1, 2, value)
        await on_progress(1, 2, "等待退出验证")
        await asyncio.Event().wait()
    assert completed_batches == [value]
    (self.root / "resumed-original-snapshot.txt").write_text("使用原任务快照")
    return value, {"total_tokens": 0}
Provider.architecture = fixed
main()
'''
# Only replace the worker command. The actual CLI, local-instance protocol,
# HTTP security, lifecycle, queue, durable checkpoint and process container run.
service_code = '''
import asyncio, sys
from codescape.__main__ import main
original = asyncio.create_subprocess_exec
async def create(*args, **kwargs):
    if len(args) >= 3 and args[-1] == "codescape.ai_worker":
        return await original(sys.executable, "-c", WORKER, **kwargs)
    return await original(*args, **kwargs)
asyncio.create_subprocess_exec = create
sys.argv = ["codescape", "--no-browser"]
main()
'''.replace("WORKER", repr(worker))


def launch():
    log = (root / f"service-{uuid.uuid4()}.log").open("wb")
    process = subprocess.Popen([sys.executable, "-c", service_code], cwd=repository, env=environment,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    log.close()
    path = root / "run/launch.json"
    for _ in range(150):
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            client = httpx.Client(base_url=value["url"].split("/#")[0], trust_env=False, timeout=10)
            try:
                response = client.post("/api/v1/session/exchange", json={"nonce": value["url"].split("#nonce=")[1]})
                if response.status_code == 200:
                    client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
                    return process, client
            except httpx.NetworkError:
                pass
            client.close()
        if process.poll() is not None:
            raise RuntimeError("独立验证服务启动失败，详见自有日志")
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=10)
    raise RuntimeError("验证服务启动超时")


def poll(client, url, expected):
    for _ in range(150):
        value = client.get(url).json()["data"]
        if value["status"] == expected:
            return value
        time.sleep(0.1)
    raise AssertionError(f"验证对象未进入 {expected}")


process, client = launch()
try:
    project = client.post("/api/v1/imports", json={"path": str(source)}).json()["data"]
    url = "/api/v1/projects/" + project["id"]
    poll(client, url, "indexed")
    assert client.post(url + "/consent", json={"accepted": True}).status_code == 200
    response = client.post(url + "/analyze", json={})
    assert response.status_code == 200
    job_id = response.json()["data"]["task_id"]
    checkpoint = root / "projects" / project["id"] / "architecture-checkpoint.json"
    for _ in range(100):
        if checkpoint.exists():
            break
        time.sleep(0.1)
    assert len(json.loads(checkpoint.read_text(encoding="utf-8"))["batches"]) == 1
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    for identity in (first, second):
        assert client.post("/api/v1/clients/heartbeat", json={"client_id": identity}).status_code == 200
    client.post("/api/v1/clients/disconnect", json={"client_id": first})
    time.sleep(5)
    assert process.poll() is None
    assert next(job for job in client.get("/api/v1/tasks").json()["data"] if job["id"] == job_id)["status"] == "running"
    client.post("/api/v1/clients/heartbeat", json={"client_id": second})
    client.post("/api/v1/clients/disconnect", json={"client_id": second})
    started = time.monotonic()
    print("分析已保存一批；其他活动页面未误停。等待最后页面失联 120+60 秒退出。", flush=True)
    process.wait(timeout=215)
    elapsed = round(time.monotonic() - started, 2)
    assert process.returncode == 0 and 179 <= elapsed <= 211
    client.close()
    database = Database(root)
    assert database.get_job(job_id)["status"] == "paused"
    assert database.get(project["id"])["status"] == "paused"
    assert RequestLedger(database).summary()["request_count"] == 0
    database.engine.dispose()
    (source / "owned.py").write_text("def changed():\n    return 2\n", encoding="utf-8", newline="")
    process, client = launch()
    assert not (root / "resumed-original-snapshot.txt").exists()
    assert next(job for job in client.get("/api/v1/tasks").json()["data"] if job["id"] == job_id)["status"] == "paused"
    assert len(json.loads(checkpoint.read_text(encoding="utf-8"))["batches"]) == 1
    assert client.post(f"/api/v1/tasks/{job_id}/start", json={}).status_code == 200
    detail = poll(client, url, "ready")
    assert (root / "resumed-original-snapshot.txt").exists() and not checkpoint.exists()
    assert [entity["name"] for entity in detail["analysis"]["entities"] if entity["visible"]] == ["original"]
    result = {"test": "真实 Windows CLI 与 AI 工作进程，应用自有固定响应，无模型请求", "exit_seconds": elapsed,
        "other_page_kept_running": True, "exit_code": 0, "checkpoint_batches": 1, "reopen_auto_requested": False,
        "resumed_same_snapshot_after_original_changed": True, "model_requests": 0}
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
finally:
    client.close()
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
