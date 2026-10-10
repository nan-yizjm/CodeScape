"""重用应用自有、固定 Commit 的 Hiredis 验证快照；只运行 CodeScape 解析器。"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from codescape.analyzers import cpp_analysis
from codescape.database import Database
from codescape.snapshots import read_snapshot

repository = Path(__file__).resolve().parents[1]
report = json.loads((repository / "docs/reports/p0/Windows独立运行验证.json").read_text(encoding="utf-8"))
previous_path = repository / "docs/reports/p0/C工程语义解析验证.json"
if previous_path.exists():
    previous_report = json.loads(previous_path.read_text(encoding="utf-8"))
    if previous_report.get("data_root"):
        report["data_root"] = previous_report["data_root"]
root = Path(report["data_root"])
database = Database(root)
try:
    project = next(item for item in database.list_projects() if item["snapshot"].get("origin", {}).get("kind") == "git")
finally:
    database.engine.dispose()
manifest = project["snapshot"]
assert manifest["origin"]["commit"] == "058ebcdf36c5b05b759c613564fb958ec363c90f"
snapshot = root / "projects" / project["id"] / "snapshots" / manifest["id"]
files = read_snapshot(snapshot, manifest)
started = time.perf_counter()
value = cpp_analysis(snapshot, files, "engineering-validation", manifest.get("source"))
elapsed = time.perf_counter() - started
assert files == read_snapshot(snapshot, manifest)
result = {"date": "2026-10-11", "repository": manifest["origin"], "snapshot_hash": manifest["hash"],
    "data_root": str(root), "snapshot_id": manifest["id"],
    "previous_confirmed_calls": len([item for item in project["result"]["calls"] if item["resolution"] == "confirmed"]),
    "files": len(files), "visible_functions": len([item for item in value.entities if item.visible]),
    "confirmed_calls": len([item for item in value.calls if item.resolution == "confirmed"]),
    "call_resolution": dict(Counter(item.resolution for item in value.calls)),
    "diagnostics": dict(Counter(item.category for item in value.diagnostics)), "elapsed_seconds": round(elapsed, 3),
    "tools": value.tools, "snapshot_bytes_unchanged": True, "target_code_executed": False,
    "model_requests": 0, "boundary": "静态工程测量，未生成 AI 架构；缺失第三方声明和条件分支仍明确诊断。",
    "diagnostic_examples": [item.model_dump() for item in value.diagnostics[:8]]}
(repository / "docs/reports/p0/C工程语义解析验证.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
