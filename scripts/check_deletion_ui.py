"""仅生成应用自有删除确认测试样例；不调用模型或执行被分析源码。"""
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime" / "deletion-ui-validation" / str(uuid.uuid4())
source = root / "original"
source.mkdir(parents=True)
(source / "input.py").write_text("raise RuntimeError('禁止执行')\ndef read_input(): return 1\n", encoding="utf-8")
database = Database(root)
for name in ["删除确认人工样例", "删除确认人工样例（副本）"]:
    project_id, version = str(uuid.uuid4()), str(uuid.uuid4())
    manifest = create_snapshot(source, root / "projects" / project_id)
    snapshot_root = root / "projects" / project_id / "snapshots" / manifest["id"]
    analysis = analyze(snapshot_root, read_snapshot(snapshot_root, manifest), version)
    architecture = Architecture(modules=[Module(id="input", name="输入读取", summary="人工预置删除验证：读取一个固定值。",
        code_paths=["input.py"], entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
        overview=Overview(introduction="应用自有删除确认测试样例，未调用模型。", capabilities=["读取样例输入"],
            principle="仅用于检验删除范围与独立副本。", limitations=["人工预置架构。"], synthesized=True))
    database.insert({"id":project_id,"name":name,"source":str(source),"created_at":now(),"status":"ready",
        "snapshot":manifest,"version_id":version,"result":analysis.model_dump(),"architecture":architecture.model_dump(),"demo":1})
database.engine.dispose()
uvicorn.run(create_app(root,"deletion-ui-test"),host="127.0.0.1",port=8126,log_level="warning")
