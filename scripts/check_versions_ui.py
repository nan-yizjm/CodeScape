"""独立候选发布浏览器验证；全部模型响应固定，无云端请求。"""
import asyncio
import uuid
from pathlib import Path

import uvicorn
from codescape.analyzers import analyze
from codescape.app import create_app
from codescape.database import Database, now
from codescape.models import Architecture, Module, Overview
from codescape.provider import Provider
from codescape.snapshots import create_snapshot, read_snapshot

repository = Path(__file__).resolve().parents[1]
root = repository / ".runtime" / "versions-ui-validation" / str(uuid.uuid4())
source = root / "source"
source.mkdir(parents=True)
(source / "a.py").write_bytes(b"def keep(): return 1\ndef change(): return 2\n")
database = Database(root)
project_id, version = str(uuid.uuid4()), str(uuid.uuid4())
manifest = create_snapshot(source, root / "projects" / project_id)
manifest["origin"] = {"kind": "local", "path": str(source)}
snapshot_root = root / "projects" / project_id / "snapshots" / manifest["id"]
analysis = analyze(snapshot_root, read_snapshot(snapshot_root, manifest), version)


def fixed_result(analysis):
    return Architecture(modules=[Module(id="fixed-data", name="数据转换样例", summary="固定响应验证：仅用于检验版本与学习迁移。",
        code_paths=["a.py"], entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
        overview=Overview(introduction="固定响应样例，未调用真实模型。", capabilities=["检验版本发布"],
                          principle="读取候选快照并发布固定架构。", limitations=["不可作为模型质量验收。"], synthesized=True))


database.insert({"id": project_id, "name": "版本迁移固定响应样例", "source": str(source), "created_at": now(),
    "status": "ready", "snapshot": manifest, "version_id": version, "result": analysis.model_dump(),
    "architecture": fixed_result(analysis).model_dump(), "demo": 1})
database.engine.dispose()
# This modifies only the application-owned validation fixture.
(source / "a.py").write_bytes(b"# ordinary comment\ndef keep(): return 1\ndef change(): return 9\ndef added(): return 4\n")


async def fixed_architecture(self, analysis, files, **callbacks):
    await asyncio.sleep(1)
    return fixed_result(analysis), {"total_tokens": 12}


Provider.architecture = fixed_architecture
Provider(root).configure("https://example.invalid", "固定响应验证", "本地验证假密钥")
uvicorn.run(create_app(root, "versions-ui-test"), host="127.0.0.1", port=8126, log_level="warning")
