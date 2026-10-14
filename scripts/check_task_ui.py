"""固定响应浏览器验收服务：独立数据目录，不发送任何模型请求。"""
import argparse
import asyncio
import json
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze, stable_id
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="应用自有固定响应界面验证，无模型请求")
parser.add_argument("--data-dir", type=Path, default=repository / ".runtime/task-ui-validation")
parser.add_argument("--port", type=int, default=8126)
args = parser.parse_args()
root = args.data_dir
database = Database(root)
for index in range(1, 4):
    project_id = stable_id("task-ui-validation", index)
    if database.get(project_id):
        continue
    manifest = create_snapshot(repository / "tests/fixtures/learning_project", root / "projects" / project_id)
    snapshot_root = root / "projects" / project_id / "snapshots" / manifest["id"]
    version = stable_id(project_id, "version")
    analysis = analyze(snapshot_root, read_snapshot(snapshot_root, manifest), version)
    database.insert({"id": project_id, "name": f"固定响应验证样例 {index}", "source": "预置样例 · 无云端请求",
                     "created_at": now(), "status": "indexed", "snapshot": manifest,
                     "version_id": version, "result": analysis.model_dump(), "demo": 1})
database.engine.dispose()


async def fixed_architecture(self, analysis, files, **callbacks):
    result = Architecture(
        modules=[Module(id="test-module", name="固定响应流程验证", summary="只验证任务交互，不代表真实 AI 分析。",
                        entity_ids=[entity.id for entity in analysis.entities if entity.visible],
                        code_paths=list(files))],
        overview=Overview(introduction="固定响应测试数据，未调用任何 AI 服务。", capabilities=["测试暂停与队列"],
                          principle="首轮等待暂停，恢复后复用检查点并完成。", limitations=["不可作为模型能力验收。"]),
    )
    if callbacks.get("completed_batches"):
        return result, {}
    await callbacks["on_batch"](1, 1, result)
    await callbacks["on_progress"](1, 1, "固定响应验证 · 等待暂停或取消")
    await asyncio.Event().wait()


Provider.architecture = fixed_architecture
async def forbidden_request(*args, **kwargs):
    raise AssertionError("固定响应浏览器验证禁止模型请求")
Provider.request = forbidden_request
Provider(root).configure("https://example.invalid", "固定响应验证", "本地验证假密钥")
nonce = "fixed-response-ui-validation"
(root / "launch.json").write_text(json.dumps({"url": f"http://127.0.0.1:{args.port}/#nonce={nonce}"}), encoding="utf-8")
uvicorn.run(create_app(root, nonce, architecture_runner=lambda provider,*args,**kwargs: provider.architecture(*args,**kwargs)), host="127.0.0.1", port=args.port, log_level="warning")
