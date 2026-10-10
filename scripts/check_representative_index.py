"""固定代表性源码快照测量；不执行、构建、安装或测试被分析工程。"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path

from codescape.imports import export_git_objects, run_git, validate_git_url
from codescape.models import Analysis
from codescape.runtime import worker_command
from codescape.snapshots import create_snapshot, matches_scope, read_snapshot
from codescape.workers import WorkerJob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--include-bundled-headers", action="store_true", help="仅 spdlog 对照测量，纳入仓库自带 fmt 头文件，不安装依赖")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    sources = json.loads((repository / "docs/benchmark_sources.json").read_text(encoding="utf-8"))
    selected = next(item for item in sources["repositories"] if item["id"] == args.id)
    if args.include_bundled_headers and selected['id'] != 'spdlog-cpp':
        parser.error('纳入 bundled 头文件的对照仅支持固定 spdlog 样本。')
    root = repository / "output/representative-index" / str(uuid.uuid4())
    root.mkdir(parents=True, exist_ok=False)
    url = validate_git_url(selected["url"])
    objects = root / "objects.git"
    run_git("init", "--bare", "--template=", str(objects))
    run_git("--git-dir", str(objects), "fetch", "--depth=1", "--no-tags", "--", url, selected["commit"])
    commit = run_git("--git-dir", str(objects), "rev-parse", "FETCH_HEAD").decode().strip()
    assert commit == selected["commit"]
    source = root / "source"
    excluded = export_git_objects(objects, source, commit)
    outside = sorted(path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()
        and not matches_scope(path.relative_to(source).as_posix(), selected["initial_includes"]))
    exclusions = [pattern for pattern in selected['initial_excludes'] if not (args.include_bundled_headers and pattern == 'include/spdlog/fmt/bundled/**')]
    manifest = create_snapshot(source, root / "project", excludes=exclusions + outside,
        includes=selected["initial_includes"])
    manifest["origin"] = {"kind": "git", "url": url, "commit": commit,
        "default_branch_at_selection": selected["default_branch_at_selection"], "excluded": excluded}
    snapshot = root / "project/snapshots" / manifest["id"]
    files = read_snapshot(snapshot, manifest)
    started = time.perf_counter()
    job = WorkerJob()
    process = subprocess.Popen(worker_command("codescape.index_worker", "--index-worker"), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job.attach(process.pid)
        stdout, _ = process.communicate(json.dumps({"root": str(snapshot), "manifest": manifest,
            "version": "representative-index"}).encode("utf-8"), timeout=300)
        assert process.returncode == 0, "CodeScape 解析工作进程失败"
        value = Analysis.model_validate_json(stdout)
    finally:
        job.close()
        process.wait(timeout=10)
    elapsed = time.perf_counter() - started
    assert files == read_snapshot(snapshot, manifest)
    assert all((source / path).read_bytes() == (snapshot / path).read_bytes() for path in files)
    result = {"date": "2026-10-11", "id": selected["id"], "root": str(root), "origin": manifest["origin"],
        "files": len(files), "snapshot_hash": manifest["hash"], "scope_hash": manifest["scope_hash"],
        "bundled_headers_included": args.include_bundled_headers,
        "visible_functions": sum(entity.visible for entity in value.entities),
        "hidden_functions": sum(not entity.visible for entity in value.entities),
        "call_resolution": dict(Counter(call.resolution for call in value.calls)),
        "diagnostics": dict(Counter(item.category for item in value.diagnostics)),
        "elapsed_seconds": round(elapsed, 3), "tools": value.tools,
        "model_requests": 0, "target_code_executed": False, "snapshot_and_source_unchanged": True,
        "boundary": "仅静态测量，第三方依赖未安装；未生成 AI 架构，不是逐条人工正确性核对。"}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "static-analysis.json").write_text(value.model_dump_json(), encoding="utf-8")
    suffix = '带依赖' if args.include_bundled_headers else ''
    (repository / f"docs/reports/p0/{selected['id']}{suffix}静态测量.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
