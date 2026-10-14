"""从锁定的官方来源准备应用自有构建资源；不安装或运行被分析项目。"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

repository = Path(__file__).resolve().parents[1]
stage = repository / ".runtime/packaging"
resources = stage / "resources"


def download(name, record):
    directory = stage / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / record["url"].split("/")[-1]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        temporary = path.with_suffix(path.suffix + ".tmp")
        request = urllib.request.Request(record["url"], headers={"User-Agent": "CodeScape-packaging"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"{name} 下载哈希与官方锁定值不一致，禁止使用。")
        temporary.replace(path)
    print(f"{name} 官方来源与 SHA-256 已核验", flush=True)
    return path


def extract_git(path):
    target = resources / "runtime/git"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            relative = PurePosixPath(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or ":" in entry.filename:
                raise ValueError("运行包包含越界路径。")
            destination = target.joinpath(*relative.parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError("运行包路径越界。")
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
    assert (target / "cmd/git.exe").is_file()


def extract_headers(path):
    target = resources / "runtime/clang"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        prefix = "llvm-mingw-20240619-ucrt-x86_64/"
        for entry in archive.infolist():
            relative = entry.filename.removeprefix(prefix)
            keep = relative.startswith(("include/", "lib/clang/18/include/"))
            keep = keep or PurePosixPath(relative).name.upper().startswith(("COPYING", "LICENSE", "NOTICE"))
            if entry.is_dir() or not entry.filename.startswith(prefix) or not keep:
                continue
            parts = PurePosixPath(relative).parts
            if ".." in parts or "\\" in relative or ":" in relative:
                raise ValueError("声明包路径越界。")
            destination = target.joinpath(*parts)
            if not destination.resolve().is_relative_to(target.resolve()):
                raise ValueError("声明包路径越界。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = archive.read(entry)
            # A live native parser may memory-map an identical trusted header.
            # Verify its bytes, then reuse it rather than opening it for writing.
            if not destination.is_file() or destination.read_bytes() != content:
                destination.write_bytes(content)
    files = {item.relative_to(target).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(target.rglob("*")) if item.is_file() and item.name != "headers-manifest.json"}
    (target / "headers-manifest.json").write_text(json.dumps({"files": files, "compiler_major": 18,
        "include_roots": ["include/c++/v1", "lib/clang/18/include", "include"]}, indent=2), encoding="utf-8")


def licenses(records):
    target = resources / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    packages = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda item: item.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        paths = []
        for file in dist.files or []:
            if ".dist-info/" not in str(file).replace("\\", "/"):
                continue
            if any(word in file.name.lower() for word in ("license", "copying", "notice", "authors")):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    relative = str(file).replace("\\", "/").split(".dist-info/", 1)[1]
                    saved = target / name / relative
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, saved)
                    paths.append(saved.relative_to(resources).as_posix())
        packages.append({"name": name, "version": dist.version,
            "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"), "license_files": paths})
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("Python 运行时许可证缺失，禁止发布。")
    shutil.copyfile(python_license, target / "Python-LICENSE.txt")
    javascript = []
    lock = json.loads((repository / "package-lock.json").read_text(encoding="utf-8"))
    for location, record in sorted(lock["packages"].items()):
        if "node_modules/" not in location:
            continue
        directory = repository / location
        saved_paths = []
        for source in directory.glob("*"):
            if source.is_file() and any(word in source.name.lower() for word in ("license", "copying", "notice")):
                saved = target / "javascript" / location / source.name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, saved)
                saved_paths.append(saved.relative_to(resources).as_posix())
        javascript.append({"path": location, "version": record.get("version"),
            "license": record.get("license"), "license_files": saved_paths})
    (resources / "third-party-components.json").write_text(json.dumps({"python_packages": packages,
        "python_runtime": {"version": sys.version.split()[0], "license_file": "licenses/Python-LICENSE.txt"},
        "javascript_packages": javascript,
        "runtime_sources": records, "note": "清单包含构建环境依赖；Inno Setup 和 PyInstaller 构建工具不会作为运行库提供。"},
        ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    records = json.loads((repository / "packaging/runtime_sources.json").read_text(encoding="utf-8"))
    resources.mkdir(parents=True, exist_ok=True)
    node = download("node", records["node"])
    with zipfile.ZipFile(node) as archive:
        prefix = f"node-v{records['node']['version']}-win-x64/"
        target = resources / "runtime/node"
        target.mkdir(parents=True, exist_ok=True)
        for name in ["node.exe", "LICENSE"]:
            (target / name).write_bytes(archive.read(prefix + name))
    extract_git(download("git", records["git"]))
    extract_headers(download("clang_headers", records["clang_headers"]))
    download("inno", records["inno"])
    licenses(records)
    print("Windows x64 构建资源已准备；仅应用自有目录。", flush=True)


if __name__ == "__main__":
    main()
