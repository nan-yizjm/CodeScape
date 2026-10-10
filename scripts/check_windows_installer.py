"""开发机安装/运行中覆盖升级/卸载验证；数据根与安装目录均为应用自有临时目录。"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import time
import uuid
import winreg
from pathlib import Path

import httpx

repository = Path(__file__).resolve().parents[1]
registry_key = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{DEEF7805-92F7-4EAC-8171-67D647C72D35}_is1"


def registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_key):
            return True
    except FileNotFoundError:
        return False


def main():
    if registered():
        raise RuntimeError("已有正式安装记录，本验证不会覆盖用户的安装。")
    build = json.loads((repository / ".runtime/packaging/latest-build.json").read_text(encoding="utf-8"))
    setup = next(Path(build["directory"]).glob("*-Setup.exe"))
    assert hashlib.sha256(setup.read_bytes()).hexdigest() == build["artifacts"][setup.name]["sha256"]
    root = repository / "output/windows-installer-validation" / str(uuid.uuid4())
    root.mkdir(parents=True)
    installed = root / "installed"
    data = root / "data"
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(CODESCAPE_DATA_DIR=str(data), PATH=os.path.join(os.environ["SystemRoot"], "System32"))
    service = None
    client = None

    def run(arguments, name, timeout=120):
        with (root / f"{name}.log").open("wb") as output:
            result = subprocess.run(arguments, env=environment, cwd=root, stdout=output, stderr=output,
                timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, f"{name} 失败，查看应用自有日志目录 {root}"
        print(f"{name} 通过", flush=True)

    def install(name):
        run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART", "/NOICONS",
            f"/DIR={installed}", f"/LOG={root / (name + '-setup.log')}"], name)

    try:
        install("首次安装")
        assert registered()
        executable = installed / "CodeScape.exe"
        assert executable.is_file() and (installed / "unins000.exe").is_file()
        manifest = json.loads((installed / "SHA256-files.json").read_text(encoding="utf-8"))
        for path, expected in manifest.items():
            assert hashlib.sha256((installed / path).read_bytes()).hexdigest() == expected, path
        log = (root / "installed-service.log").open("wb")
        service = subprocess.Popen([str(executable), "--no-browser"], cwd=root, env=environment,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        log.close()
        for _ in range(300):
            launch_path = data / "run/launch.json"
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
            assert service.poll() is None, "安装后程序未能启动"
            time.sleep(0.1)
        assert client is not None
        project = client.get("/api/v1/projects").json()["data"][0]
        url = f"/api/v1/projects/{project['id']}"
        detail = client.get(url).json()["data"]
        entity = next(item for item in detail["analysis"]["entities"] if item["visible"])
        assert client.put(f"{url}/learning/{entity['id']}", json={"version_id": project["version_id"],
            "state": "mastered", "expected_revision": 0}).status_code == 200
        assert client.get(url).json()["data"]["progress"]["mastered"] == 1
        snapshot_before = {str(path.relative_to(data)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (data / "projects").rglob("*") if path.is_file()}
        client.close()
        client = None
        # The installer must stop this active service through its authenticated protocol.
        assert service.poll() is None
        install("运行中覆盖升级")
        service.wait(timeout=5)
        assert service.returncode == 0
        with sqlite3.connect(f"{(data / 'app.db').as_uri()}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT state FROM prototype_learning WHERE entity_id=?", (entity["id"],)).fetchone()[0] == "mastered"
            assert connection.execute("SELECT count(*) FROM ai_requests").fetchone()[0] == 0
        run([str(installed / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={root / 'uninstall.log'}"], "卸载")
        assert not executable.exists() and not registered()
        assert (data / "app.db").is_file()
        snapshot_after = {str(path.relative_to(data)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (data / "projects").rglob("*") if path.is_file()}
        assert snapshot_before == snapshot_after
        report = {"date": "2026-10-11", "platform": platform.platform(), "build_id": build["build_id"],
            "setup_sha256": build["artifacts"][setup.name]["sha256"], "validation_root": str(root),
            "first_install": "通过", "installed_files_match_manifest": True,
            "upgrade_while_running": "通过；应用先正常退出，返回码 0", "learning_retained": True,
            "uninstall": "通过；程序和本次安装记录移除", "data_and_snapshot_retained": True,
            "developer_path_removed": True, "model_requests": 0, "signed": False,
            "boundary": "开发机 Windows 11 x64 验证，同版本覆盖升级；不代替干净 Windows 10/11 与跨版本升级验收。"}
        (repository / "docs/reports/p0/Windows安装升级卸载验证.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if client:
            client.close()
        if service and service.poll() is None:
            service.terminate()
            service.wait(timeout=15)
        # Retain own failed installation/logs for diagnosis rather than silently reporting success.


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
