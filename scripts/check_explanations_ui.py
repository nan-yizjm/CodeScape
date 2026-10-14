"""读取既有真实模型报告展示函数讲解；浏览验证不发送新模型请求。"""
import json
import uuid

import uvicorn
from codescape.analyzers import REPOSITORY, python_analysis
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Analysis, Architecture
from codescape.provider import Provider, validate_architecture
from codescape.snapshots import create_snapshot, read_snapshot

report = json.loads((REPOSITORY / "docs/reports/p0/deepseek函数讲解验证.json").read_text(encoding="utf-8"))
analysis = Analysis.model_validate(report["analysis"])
architecture = Architecture.model_validate(report["architecture"])
root = REPOSITORY / ".runtime" / "explanations-ui-validation" / str(uuid.uuid4())
project_id = str(uuid.uuid4())
source = root / "uploaded-source"
source.mkdir(parents=True)
# The verification script used read_text (LF normalization). Preserve exactly
# those uploaded text bytes in this application-owned UI fixture.
for path in ("input.py", "engine.py", "presentation.py"):
    text = (REPOSITORY / "tests/fixtures/learning_project" / path).read_text(encoding="utf-8")
    (source / path).write_bytes(text.encode("utf-8"))
manifest = create_snapshot(source, root / "projects" / project_id)
files = read_snapshot(root / "projects" / project_id / "snapshots" / manifest["id"], manifest)
assert set(files) == {"input.py", "engine.py", "presentation.py"}
checked = python_analysis(files, report["version_id"])
assert checked.file_hashes == analysis.file_hashes
for field in ("entities", "scopes", "calls"):
    assert {item.id: item.model_dump() for item in getattr(checked, field)} == {item.id: item.model_dump() for item in getattr(analysis, field)}
validate_architecture(architecture, analysis, set(files))
database = Database(root)
database.insert({"id": project_id, "name": "DeepSeek 函数讲解验证样例", "source": "已授权预置源码 · 既有真实模型结果",
                 "created_at": now(), "status": "ready", "snapshot": manifest, "version_id": report["version_id"],
                 "result": analysis.model_dump(), "architecture": architecture.model_dump(), "demo": 1})
database.engine.dispose()


async def forbidden_request(*args, **kwargs):
    (root / "unexpected-model-request.txt").write_text("浏览阶段触发了不应发生的模型请求", encoding="utf-8")
    raise RuntimeError("浏览验证禁止模型请求")


Provider.request = forbidden_request
uvicorn.run(create_app(root, "explanations-ui-test"), host="127.0.0.1", port=8126, log_level="warning")
