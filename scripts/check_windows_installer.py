"""开发机安装/运行中覆盖升级/卸载验证；数据根与安装目录均为应用自有临时目录。"""
from __future__ import annotations

import argparse
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
from datetime import UTC, datetime, timedelta
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


def changed_application_files(before, after):
    """前端等随包资源也是程序内容；构建号元数据变化本身不证明更新。"""
    metadata = {'_internal/build-info.json', 'SHA256-files.json'}
    return sorted(path for path in before.keys() | after.keys()
                  if path not in metadata and before.get(path) != after.get(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-build', type=Path, help='从应用自有已校验预发布目录安装后升级；不是跨正式版本验收')
    args = parser.parse_args()
    if registered():
        raise RuntimeError("已有正式安装记录，本验证不会覆盖用户的安装。")
    build = json.loads((repository / ".runtime/packaging/latest-build.json").read_text(encoding="utf-8"))
    setup = next(Path(build["directory"]).glob("*-Setup.exe"))
    assert hashlib.sha256(setup.read_bytes()).hexdigest() == build["artifacts"][setup.name]["sha256"]
    previous = None
    initial_setup = setup
    if args.previous_build:
        previous_root = args.previous_build.resolve(strict=True)
        previous_root.relative_to((repository/'output/releases').resolve(strict=True))
        previous = json.loads((previous_root/'发布校验.json').read_text(encoding='utf-8'))
        assert previous['build_id'] != build['build_id']
        initial_setup = next(previous_root.glob('*-Setup.exe')).resolve(strict=True)
        initial_setup.relative_to(previous_root)
        assert hashlib.sha256(initial_setup.read_bytes()).hexdigest() == previous['artifacts'][initial_setup.name]['sha256']
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

    def install(name, selected=setup):
        run([str(selected), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART", "/NOICONS",
            f"/DIR={installed}", f"/LOG={root / (name + '-setup.log')}"], name)

    def start(executable, name):
        nonlocal service, client
        log = (root / (name+'.log')).open('wb')
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

    try:
        install("首次安装", initial_setup)
        assert registered()
        executable = installed / "CodeScape.exe"
        assert executable.is_file() and (installed / "unins000.exe").is_file()
        manifest = json.loads((installed / "SHA256-files.json").read_text(encoding="utf-8"))
        for path, expected in manifest.items():
            assert hashlib.sha256((installed / path).read_bytes()).hexdigest() == expected, path
        initial_program_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        initial_build_id = json.loads((installed/'_internal/build-info.json').read_text(encoding='utf-8'))['build_id']
        assert initial_build_id == (previous or build)['build_id']
        start(executable,'installed-service')
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
        upgraded_manifest = json.loads((installed/'SHA256-files.json').read_text(encoding='utf-8'))
        expected_manifest = json.loads((repository/'.runtime/packaging/dist/CodeScape/SHA256-files.json').read_text(encoding='utf-8'))
        assert upgraded_manifest == expected_manifest
        for path, expected in upgraded_manifest.items():
            assert hashlib.sha256((installed/path).read_bytes()).hexdigest() == expected,path
        upgraded_build_id = json.loads((installed/'_internal/build-info.json').read_text(encoding='utf-8'))['build_id']
        assert upgraded_build_id == build['build_id']
        upgraded_program_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
        changed_files = changed_application_files(manifest, upgraded_manifest)
        if previous:
            assert changed_files, '两个构建的实际程序或随包资源未变化，仅构建号变化不能计更新'
        with sqlite3.connect(f"{(data / 'app.db').as_uri()}?mode=ro", uri=True) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert connection.execute("SELECT state FROM prototype_learning WHERE entity_id=?", (entity["id"],)).fetchone()[0] == "mastered"
            assert connection.execute("SELECT count(*) FROM ai_requests").fetchone()[0] == 0
        start(executable,'upgraded-service')
        assert client.get(url).json()['data']['progress']['mastered']==1
        assert client.get('/api/v1/usage').json()['data']['records']==[]
        client.close()
        client = None
        run([str(executable),'--prepare-update','--no-browser'],'升级后正常保存退出',timeout=65)
        service.wait(timeout=5)
        assert service.returncode == 0
        # The installed application's old check only knows its own revision. The new
        # installer must independently reject malformed data before replacing files.
        database_path = data / "app.db"
        backup_path = root / "owned-before-corruption.sqlite"
        with sqlite3.connect(database_path) as connection, sqlite3.connect(backup_path) as backup:
            connection.backup(backup)
            connection.execute("ALTER TABLE prototype_sessions DROP COLUMN value")
        program_before = hashlib.sha256(executable.read_bytes()).hexdigest()
        corrupt_before = hashlib.sha256(database_path.read_bytes()).hexdigest()
        with (root / "incompatible-data.log").open("wb") as output:
            rejected = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CURRENTUSER", "/NORESTART",
                "/NOICONS", f"/DIR={installed}", f"/LOG={root / 'incompatible-setup.log'}"], env=environment,
                cwd=root, stdout=output, stderr=output, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        assert rejected.returncode != 0, "不兼容数据没有阻止替换程序"
        assert hashlib.sha256(executable.read_bytes()).hexdigest() == program_before
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == corrupt_before
        with sqlite3.connect(backup_path) as backup, sqlite3.connect(database_path) as connection:
            backup.backup(connection)
        print("新安装包独立拒绝不兼容数据，程序和数据未被替换", flush=True)
        run([str(installed / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={root / 'uninstall.log'}"], "卸载")
        assert not executable.exists() and not registered()
        assert (data / "app.db").is_file()
        snapshot_after = {str(path.relative_to(data)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (data / "projects").rglob("*") if path.is_file()}
        assert snapshot_before == snapshot_after
        report = {"date": (datetime.now(UTC) + timedelta(hours=8)).date().isoformat(), "platform": platform.platform(), "build_id": build["build_id"],
            "setup_sha256": build["artifacts"][setup.name]["sha256"], "validation_root": str(root),
            "first_install": "通过", "installed_files_match_manifest": True,
            'previous_build_id':initial_build_id,'upgraded_build_id':upgraded_build_id,
            'initial_program_sha256':initial_program_hash,'upgraded_program_sha256':upgraded_program_hash,
            'different_program_bytes':upgraded_program_hash!=initial_program_hash,
            'changed_application_files':changed_files,
            'upgraded_program_started_and_learning_read':True,
            'upgraded_files_match_current_manifest':True,
            "upgrade_while_running": "通过；应用先正常退出，返回码 0", "learning_retained": True,
            "new_installer_data_preflight": "通过；字段损坏时安装返回失败，原程序与数据库哈希保持一致",
            "uninstall": "通过；程序和本次安装记录移除", "data_and_snapshot_retained": True,
            "developer_path_removed": True, "model_requests": 0, "signed": False,
            "boundary": ('开发机Windows11两个实际预发布构建升级；版本号均为0.1.0，不代替干净Windows10/11或跨正式版本验收。'
                         if previous else '开发机 Windows 11 x64 验证，同版本覆盖升级；不代替干净 Windows 10/11 与跨版本升级验收。')}
        (repository / ('docs/reports/p0/Windows跨构建升级验证.json' if previous
                       else 'docs/reports/p0/Windows安装升级卸载验证.json')).write_text(
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
