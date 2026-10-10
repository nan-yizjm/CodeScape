"""独立 Windows 程序验证；只读应用自有样例，不上传源码、不执行样例。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path

import httpx

repository = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path,
        default=repository / ".runtime/packaging/dist/CodeScape/CodeScape.exe")
    parser.add_argument("--remote", action="store_true", help="额外验证公开 Hiredis 默认分支导入；不请求模型")
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    root = repository / "output/windows-bundle-validation" / str(uuid.uuid4())
    source = root / "owned-source"
    source.mkdir(parents=True)
    files = {
        "owned.py": 'raise RuntimeError("仅用于静态读取，不得执行")\ndef target(): return 1\ndef caller(): return target()\n',
        "owned.ts": 'throw new Error("仅用于静态读取，不得执行");\nexport function tsTarget(){return 1;}\nexport function tsCaller(){return tsTarget();}\n',
        "owned.c": '#include <stddef.h>\nint cTarget(void){return 1;}\nint cCaller(void){return cTarget();}\n',
        "owned.cpp": '#include <vector>\nint cppTarget(){return 1;}\nint cppCaller(){std::vector<int> v{1};return cppTarget()+v[0];}\n',
    }
    for path, text in files.items():
        (source / path).write_text(text, encoding="utf-8", newline="")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(CODESCAPE_DATA_DIR=str(root), PATH=os.path.join(os.environ["SystemRoot"], "System32"))
    log = (root / "service.log").open("wb")
    process = subprocess.Popen([str(executable), "--no-browser"], cwd=root, env=environment,
        stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    log.close()
    client = None
    try:
        for _ in range(300):
            launch_path = root / "run/launch.json"
            if launch_path.is_file():
                launch = json.loads(launch_path.read_text(encoding="utf-8"))
                client = httpx.Client(base_url=launch["url"].split("/#")[0], trust_env=False, timeout=20)
                try:
                    response = client.post("/api/v1/session/exchange", json={"nonce": launch["url"].split("#nonce=")[1]})
                    if response.status_code == 200:
                        client.headers["x-csrf-token"] = response.json()["data"]["csrf_token"]
                        break
                except httpx.NetworkError:
                    pass
                client.close()
                client = None
            if process.poll() is not None:
                raise RuntimeError(f"独立程序启动失败，查看 {root / 'service.log'}")
            time.sleep(0.1)
        else:
            raise RuntimeError("独立程序启动超时")
        assert client.get("/").status_code == 200
        assert "<script" in client.get("/").text
        samples = client.get("/api/v1/projects").json()["data"]
        assert len(samples) == 1 and samples[0]["demo"] and samples[0]["has_architecture"]
        second = subprocess.run([str(executable), "--no-browser"], cwd=root, env=environment,
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode == 0
        assert "已复用" in second.stdout.decode("utf-8", errors="replace")
        reactivated = json.loads((root / "run/launch.json").read_text(encoding="utf-8"))
        assert reactivated["port"] == launch["port"]
        response = client.post("/api/v1/imports", json={"path": str(source)})
        assert response.status_code in {200, 202}, response.text
        project_id = response.json()["data"]["id"]
        url = f"/api/v1/projects/{project_id}"
        for _ in range(300):
            project = client.get(url).json()["data"]
            if project["status"] in {"indexed", "failed"}:
                break
            time.sleep(0.1)
        assert project["status"] == "indexed", project["error"]
        assert project["function_count"] == 8
        assert not project["has_architecture"] and not project["upload_consent"]["accepted"]
        confirmed = [call for call in project["analysis"]["calls"] if call["resolution"] == "confirmed"]
        by_language = {entity["id"]: entity["language"] for entity in project["analysis"]["entities"]}
        confirmed_languages = {by_language[call["target_id"]] for call in confirmed}
        assert confirmed_languages == {"python", "typescript", "c", "cpp"}, confirmed_languages
        original = files["owned.py"]
        (source / "owned.py").write_text("已修改的原目录，不应该影响快照", encoding="utf-8")
        text = client.get(f"{url}/files/{project['file_ids']['owned.py']}?version_id={project['version_id']}").json()["data"]
        assert text["text"] == original
        assert client.get("/api/v1/usage").json()["data"]["records"] == []
        remote = None
        if args.remote:
            response = client.post("/api/v1/imports", json={"url": "https://github.com/redis/hiredis.git"}, timeout=60)
            assert response.status_code in {200, 202}, response.text
            remote_id = response.json()["data"]["id"]
            for _ in range(600):
                remote = client.get(f"/api/v1/projects/{remote_id}").json()["data"]
                if remote["status"] in {"indexed", "failed"}:
                    break
                time.sleep(0.1)
            assert remote["status"] == "indexed", remote["error"]
            assert remote["origin"]["kind"] == "git" and len(remote["origin"]["commit"]) == 40
            assert remote["file_count"] > 0 and remote["function_count"] > 0
            assert not remote["has_architecture"] and not remote["upload_consent"]["accepted"]
            assert client.get("/api/v1/usage").json()["data"]["records"] == []
        resources = executable.parent / "_internal"
        versions = {}
        for name, command in {
            "node": [str(resources / "runtime/node/node.exe"), "--version"],
            "git": [str(resources / "runtime/git/cmd/git.exe"), "--version"],
        }.items():
            checked = subprocess.run(command, capture_output=True, env=environment, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW)
            assert checked.returncode == 0
            versions[name] = checked.stdout.decode().strip()
        # Workers must retain pipe streams after freezing. Invalid input must be a controlled protocol error.
        worker = subprocess.run([str(executable), "--ai-worker"], input=b'{}\n', capture_output=True,
            env=environment, cwd=root, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert worker.returncode == 1
        assert json.loads(worker.stdout)["event"] == "error"
        stopped = subprocess.run([str(executable), "--prepare-update", "--no-browser"], env=environment,
            cwd=root, capture_output=True, timeout=65, creationflags=subprocess.CREATE_NO_WINDOW)
        assert stopped.returncode == 0
        process.wait(timeout=5)
        assert process.returncode == 0
        result = {"date": "2026-10-11", "platform": platform.platform(), "architecture": platform.machine(),
            "executable": str(executable), "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "data_root": str(root), "developer_path_removed": True, "duplicate_launch_reused": True,
            "static_sample_functions": 8, "confirmed_call_languages": sorted(confirmed_languages),
            "original_modification_does_not_change_snapshot": True, "ai_worker_protocol": "受控错误响应",
            "runtime_versions": versions, "model_requests": 0,
            "public_git_import": ({"repository": "https://github.com/redis/hiredis.git", "origin": remote["origin"],
                "file_count": remote["file_count"], "function_count": remote["function_count"],
                "diagnostic_count": remote["diagnostic_count"], "ai_analysis": False} if remote else "本次未验证"),
            "update_saves_and_exits_normally": True,
            "boundary": "开发机 Windows 独立运行验证；不代替干净 Windows 10/11 虚拟机验收；不代表真实 AI 分析质量。"}
        report = repository / "docs/reports/p0/Windows独立运行验证.json"
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if client:
            client.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=15)


if __name__ == "__main__":
    main()
