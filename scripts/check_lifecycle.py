"""在独立测试数据中验证真实启动、重复启动和 120+60 秒自动退出。"""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from codescape.database import Database

sys.stdout.reconfigure(encoding="utf-8")
repository = Path(__file__).resolve().parents[1]
root = repository / "output/lifecycle-validation" / str(uuid.uuid4())
root.mkdir(parents=True)
environment = dict(os.environ, CODESCAPE_DATA_DIR=str(root), PYTHONIOENCODING="utf-8")
environment["PYTHONPATH"] = str(repository / "server")
log = (root / "service.log").open("wb")
process = subprocess.Popen([sys.executable, "-m", "codescape", "--no-browser"], cwd=repository,
                           env=environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    launch = root / "run/launch.json"
    for _ in range(100):
        if launch.exists():
            break
        if process.poll() is not None:
            raise RuntimeError("启动未成功，详见独立测试日志。")
        time.sleep(0.1)
    initial = json.loads(launch.read_text(encoding="utf-8"))
    base = initial["url"].split("/#")[0]
    with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
        for _ in range(100):
            try:
                response = client.get("/api/v1/session")
                if response.status_code == 401:
                    break
            except httpx.NetworkError:
                pass
            time.sleep(0.1)
        db = Database(root)
        sample = db.list_projects()[0]
        # A read-only second launch must not run startup recovery again.
        db.update(sample["id"], status="analyzing")
        duplicate = subprocess.run([sys.executable, "-m", "codescape", "--port", "8127", "--no-browser"],
                                   cwd=repository, env=environment, capture_output=True, timeout=20)
        assert duplicate.returncode == 0
        activated = json.loads(launch.read_text(encoding="utf-8"))
        assert activated["port"] == initial["port"]
        assert db.get(sample["id"])["status"] == "analyzing"
        db.update(sample["id"], status="ready")
        db.engine.dispose()
        nonce = activated["url"].split("#nonce=")[1]
        response = client.post("/api/v1/session/exchange", json={"nonce": nonce})
        assert response.status_code == 200
        client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        for identity in (first, second):
            assert client.post("/api/v1/clients/heartbeat", json={"client_id": identity}).status_code == 200
        assert client.post("/api/v1/clients/disconnect", json={"client_id": first}).status_code == 200
        time.sleep(5)
        assert process.poll() is None
        assert client.post("/api/v1/clients/heartbeat", json={"client_id": second}).status_code == 200
        assert client.post("/api/v1/clients/disconnect", json={"client_id": second}).status_code == 200
        started = time.monotonic()
        print("真实服务已启动；重复启动复用同一端口且未重置状态；等待最后页面 120+60 秒退出。", flush=True)
        process.wait(timeout=210)
        elapsed = round(time.monotonic() - started, 1)
        assert 179 <= elapsed <= 200 and process.returncode == 0
        result = {"test": "真实本地服务，无模型请求", "duplicate_reused": True,
                  "state_unchanged_by_duplicate": True, "last_page_exit_seconds": elapsed,
                  "exit_code": process.returncode}
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    log.close()
