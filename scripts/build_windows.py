"""生成固定依赖的 Windows x64 预发布安装包、便携包和 SHA-256 清单。"""
from __future__ import annotations

import gc
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

repository = Path(__file__).resolve().parents[1]
stage = repository / ".runtime/packaging"


def sha256(path):
    with path.open("rb") as value:
        return hashlib.file_digest(value, "sha256").hexdigest()


def run(arguments, name):
    log = stage / f"{name}.log"
    with log.open("wb") as output:
        completed = subprocess.run([str(item) for item in arguments], cwd=repository,
            stdout=output, stderr=output, creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode:
        raise RuntimeError(f"{name} 未通过，查看 {log}")
    print(f"{name} 完成", flush=True)


def main():
    if sys.platform != "win32" or platform.machine().upper() not in {"AMD64", "X86_64"}:
        raise RuntimeError("当前构建仅适用于 Windows x64。")
    stage.mkdir(parents=True, exist_ok=True)
    from codescape.database import Database
    from codescape.storage_schema import KNOWN_REVISIONS, SCHEMA_VERSION
    run([sys.executable, repository / "scripts/prepare_windows_runtime.py"], "prepare-runtime")
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    build_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sources = {}
    for path in [repository / "uv.lock", repository / "package-lock.json", repository / "packaging/runtime_sources.json"]:
        sources[path.relative_to(repository).as_posix()] = sha256(path)
    metadata = {"version": version, "build_id": build_id, "platform": "Windows x64", "signed": False,
        "status": "发布候选构建；功能及安装验证另行记录", "source_locks_sha256": sources,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip(),
        "data_schema": SCHEMA_VERSION, "python": sys.version.split()[0],
        "validation_boundary": "构建成功不等于干净 Windows 10/11 安装、升级与卸载验收通过。"}
    (stage / "resources/build-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=stage, prefix="schema-contract-") as temporary:
        database = Database(Path(temporary))
        contract = {"version": SCHEMA_VERSION, "revisions": KNOWN_REVISIONS,
            "columns": {name: [column.name for column in table.columns] for name, table in database.projects.metadata.tables.items()}}
        database.engine.dispose()
        gc.collect()
    (stage / "resources/schema-support.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    run([sys.executable, "-m", "PyInstaller", "packaging/Preflight.spec", "--distpath", stage / "preflight",
        "--workpath", stage / "preflight-work", "--noconfirm"], "freeze-preflight")
    run([sys.executable, "-m", "PyInstaller", "packaging/CodeScape.spec", "--distpath", stage / "dist",
        "--workpath", stage / "work", "--noconfirm"], "freeze")
    bundle = stage / "dist/CodeScape"
    manifest = {path.relative_to(bundle).as_posix(): sha256(path) for path in sorted(bundle.rglob("*")) if path.is_file()}
    (bundle / "SHA256-files.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    releases = repository / "output/releases" / build_id
    releases.mkdir(parents=True, exist_ok=False)
    compiler = stage / "inno/ISCC.exe"
    if not compiler.is_file():
        raise RuntimeError("未找到已经验证来源并按用户安装的 Inno Setup 7.1.0 编译器。")
    run([compiler, "/Q", f"/DBuildVersion={version}", f"/DBundleDir={bundle}", f"/DOutputDir={releases}",
        f"/DPreflightExe={stage / 'preflight/CodeScapeDataCheck.exe'}",
        "packaging/CodeScape.iss"], "installer")
    portable = releases / f"CodeScape-{version}-Windows-x64-Portable.zip"
    with zipfile.ZipFile(portable, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                archive.write(path, "CodeScape/" + path.relative_to(bundle).as_posix())
    artifacts = {path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
        for path in sorted(releases.iterdir()) if path.is_file()}
    metadata["artifacts"] = artifacts
    (releases / "发布校验.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (stage / "latest-build.json").write_text(json.dumps({"directory": str(releases), **metadata}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"预发布安装包、便携包与校验清单已生成：{releases}", flush=True)


if __name__ == "__main__":
    main()
