"""应用自有固定结果验证：主要协作、全部依赖、轻量预览；禁止实际模型请求。"""
import json
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.database import Database, now
from codescape.dependencies import validate_primary_dependencies
from codescape.models import Architecture, Module, Overview, PrimaryDependency
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime/dependencies-ui-validation" / str(uuid.uuid4())
source = root / "owned-source"
source.mkdir(parents=True)
(source / "owned.py").write_bytes(b"def core(): return 'result'\ndef auxiliary(): return None\ndef idle(): return None\ndef entry():\n    value = core()\n    auxiliary()\n    return value\n")
project_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
manifest = create_snapshot(source, root / "projects" / project_id)
files = read_snapshot(root / "projects" / project_id / "snapshots" / manifest["id"], manifest)
analysis = python_analysis(files, version_id)
modules = [Module(id=name, name=label, summary=summary, code_paths=["owned.py"],
    entity_ids=[entity.id for entity in analysis.entities if entity.name == name])
    for name, label, summary in [("entry", "结果入口", "取得核心结果，调用辅助操作后返回结果。"),
        ("core", "核心计算", "提供入口所需的结果文本。"), ("auxiliary", "辅助维护", "执行辅助操作，不改变入口返回的结果。"),
        ("idle", "独立工具", "当前没有与入口相连的确定调用。")]]
call = next(call for call in analysis.calls if call.expression == "core")
arch = Architecture(modules=modules, overview=Overview(introduction="人工预置的依赖阅读验证项目。",
    capabilities=["验证主要协作与全部依赖切换"], principle="读取保存产物，不执行样例代码。",
    limitations=["人工预置，不代表真实 AI 核心协作判断。"]),
    primary_dependencies=[PrimaryDependency(call_ids=[call.id], reason="人工验证：入口通过核心计算取得最终返回结果；辅助维护不改变返回值。")])
validate_primary_dependencies(arch, analysis)
db = Database(root)
db.insert({"id": project_id,"name":"依赖阅读人工验证","source":"应用自有人工结果 · 无模型请求", "created_at":now(),
    "status":"ready","snapshot":manifest,"version_id":version_id,"result":analysis.model_dump(),"architecture":arch.model_dump(),"demo":1})
db.engine.dispose()
async def forbidden(*args, **kwargs):
    (root / "unexpected-model-request.txt").write_text("意外模型请求",encoding="utf-8")
    raise AssertionError("依赖界面验证禁止模型请求")
Provider.request = forbidden
nonce = "dependencies-ui-validation"
(repository / ".runtime/dependencies-ui-launch.json").write_text(json.dumps({"url":f"http://127.0.0.1:8126/#nonce={nonce}",
    "root":str(root)}),encoding="utf-8")
uvicorn.run(create_app(root,nonce),host="127.0.0.1",port=8126,log_level="warning")
