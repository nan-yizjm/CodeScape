"""保存应用自有人工关系结果供浏览器验收；无实际模型请求，不冒充 AI 输出。"""
import json
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import python_analysis
from codescape.app import create_app
from codescape.call_judgments import validate_judgments
from codescape.database import Database, now
from codescape.models import Architecture, CallJudgment, Module, Overview
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime/judgments-ui-validation" / str(uuid.uuid4())
source = root / "owned-source"
source.mkdir(parents=True)
(source / "owned.py").write_text('''class Codec:
    @staticmethod
    def encode(value):
        return str(value)
def shared():
    return 1
def render():
    return Codec.encode(1)
def direct():
    return shared()
''', encoding="utf-8", newline="")
project_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
manifest = create_snapshot(source, root / "projects" / project_id)
files = read_snapshot(root / "projects" / project_id / "snapshots" / manifest["id"], manifest)
analysis = python_analysis(files, version_id)
call = next(call for call in analysis.calls if call.expression == "Codec.encode")
target = next(entity for entity in analysis.entities if entity.name == "encode")
architecture = Architecture(modules=[
    Module(id="callers", name="输入处理", summary="调用编码和公共计算，展示两种确认来源。",
        code_paths=["owned.py"], entity_ids=[entity.id for entity in analysis.entities if entity.name in {"render", "direct"}]),
    Module(id="targets", name="编码与计算", summary="将输入转成文本，并提供一个固定数值。",
        code_paths=["owned.py"], entity_ids=[entity.id for entity in analysis.entities if entity.name in {"encode", "shared"}])],
    overview=Overview(introduction="人工预置的关系溯源样例，尚未经过 AI 生成。", capabilities=["验证混合来源与源码跳转"],
        principle="只验证保存结果的界面展示，不执行样例代码。", limitations=["人工预置，不代表真实模型判断质量。"]),
    call_judgments=[CallJudgment(call_id=call.id, target_id=target.id,
        reason="人工预置判断：Codec 在本文件定义，encode 声明为静态方法；render 以类名调用，目标实现将输入转换成文本。",
        evidence=[{"path": "owned.py", "start_line": 1, "end_line": 4}, {"path": "owned.py", "start_line": 7, "end_line": 8}])])
validate_judgments(architecture, analysis, {call.id}, files, {"owned.py": [(1, 10)]})
database = Database(root)
database.insert({"id": project_id, "name": "关系溯源人工验证", "source": "应用自有人工结果 · 无模型请求",
    "created_at": now(), "status": "ready", "snapshot": manifest, "version_id": version_id,
    "result": analysis.model_dump(), "architecture": architecture.model_dump(), "demo": 1})
database.engine.dispose()
async def forbidden(*args, **kwargs):
    (root / "unexpected-model-request.txt").write_text("意外模型请求", encoding="utf-8")
    raise AssertionError("人工关系界面验证禁止模型请求")
Provider.request = forbidden
nonce = "judgments-ui-validation"
(repository / ".runtime/judgments-ui-launch.json").write_text(json.dumps({"url": f"http://127.0.0.1:8126/#nonce={nonce}",
    "root": str(root)}), encoding="utf-8")
uvicorn.run(create_app(root, nonce), host="127.0.0.1", port=8126, log_level="warning")
