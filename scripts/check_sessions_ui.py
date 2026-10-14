"""探索恢复浏览器夹具；人工架构和应用自有只读源码，没有模型请求。"""
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.snapshots import create_snapshot, read_snapshot

root = Path(__file__).resolve().parents[1] / ".runtime" / "sessions-ui-validation" / str(uuid.uuid4())
source = root / "source"
source.mkdir(parents=True)
text = "raise RuntimeError('禁止执行验证源码')\ndef read_input():\n"
text += "".join(f"    value_{index:03d} = {index}\n" for index in range(1,141))
text += "    return value_140\n\ndef transform():\n    return read_input()\n"
(source / "a.py").write_text(text,encoding="utf-8")
database = Database(root)
for name in ["探索恢复人工样例", "探索恢复人工样例（副本）"]:
    project_id, version = str(uuid.uuid4()), str(uuid.uuid4())
    manifest = create_snapshot(source, root / "projects" / project_id)
    manifest["origin"] = {"kind":"local","path":str(source)}
    snapshot = root / "projects" / project_id / "snapshots" / manifest["id"]
    analysis = analyze(snapshot, read_snapshot(snapshot,manifest),version)
    entities = {entity.name:entity.id for entity in analysis.entities if entity.visible}
    architecture = Architecture(modules=[
        Module(id="flow",name="数据处理人工样例",summary="仅用于探索恢复验证，架构人工预置。",code_paths=["a.py"]),
        Module(id="input",parent_id="flow",name="输入读取",summary="准备一组验证数据。",code_paths=["a.py"],entity_ids=[entities["read_input"]]),
        Module(id="output",parent_id="flow",name="输出转换",summary="调用输入读取并返回数据。",code_paths=["a.py"],entity_ids=[entities["transform"]])],
        overview=Overview(introduction="探索恢复人工样例，不包含真实模型分析。",capabilities=["验证层级与面板恢复"],
                          principle="人工模块与实际静态调用仅用于验证交互。",limitations=["人工预置，不能作为模型质量验收。"],synthesized=True))
    database.insert({"id":project_id,"name":name,"source":str(source),"created_at":now(),"status":"ready",
        "snapshot":manifest,"version_id":version,"result":analysis.model_dump(),"architecture":architecture.model_dump(),"demo":1})
database.engine.dispose()
uvicorn.run(create_app(root,"sessions-ui-test"),host="127.0.0.1",port=8126,log_level="warning")
