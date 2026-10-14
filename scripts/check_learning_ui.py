"""无函数模块学习状态浏览器验收；人工预置架构，无模型请求。"""
import argparse
import asyncio
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime" / "learning-ui-validation" / str(uuid.uuid4())
source = root / "source"
source.mkdir(parents=True)
(source / "config.py").write_bytes(b"DEFAULT_LIMIT = 20\nDEBUG = False\n")
(source / "engine.py").write_bytes(b"def transform(value):\n    return value * 2\n")
(source / "unknown.py").write_bytes(b"# no readable implementation\n")
project_id, version = str(uuid.uuid4()), str(uuid.uuid4())
manifest = create_snapshot(source, root / "projects" / project_id)
snapshot_root = root / "projects" / project_id / "snapshots" / manifest["id"]
analysis = analyze(snapshot_root, read_snapshot(snapshot_root, manifest), version)
modules = [Module(id="root", name="数据处理样例", summary="组织配置与数值转换。", code_paths=["config.py", "engine.py"]),
           Module(id="config", parent_id="root", name="配置定义", summary="声明默认处理数量和调试开关。", code_paths=["config.py"]),
           Module(id="engine", parent_id="root", name="数值转换", summary="将输入数值扩大两倍。", code_paths=["engine.py"],
                  entity_ids=[entity.id for entity in analysis.entities if entity.visible]),
           Module(id="unknown", parent_id="root", name="未知范围", summary="缺少可读实现，等待补充。", code_paths=["unknown.py"], incomplete=True)]
architecture = Architecture(modules=modules, overview=Overview(introduction="人工预置学习边界验证样例，不代表真实模型分析。",
    capabilities=["声明配置", "数值转换"], principle="配置和转换分开组织，未知范围独立标记。", limitations=["人工预置架构，未调用模型。"], synthesized=True))
database = Database(root)
database.insert({"id": project_id, "name": "模块学习人工验证样例", "source": str(source), "created_at": now(),
    "status": "ready", "snapshot": manifest, "version_id": version, "result": analysis.model_dump(),
    "architecture": architecture.model_dump(), "demo": 1})
database.engine.dispose()
parser = argparse.ArgumentParser(description="人工学习状态浏览器验证，不发送模型请求")
parser.add_argument("--write-delay", type=float, default=0)
args = parser.parse_args()
app = create_app(root, "learning-ui-test")
if args.write_delay:
    @app.middleware("http")
    async def delayed_learning(request, call_next):
        if request.method == "PUT" and "/learning/" in request.url.path:
            await asyncio.sleep(min(5, max(0, args.write_delay)))
        return await call_next(request)
uvicorn.run(app, host="127.0.0.1", port=8126, log_level="warning")
