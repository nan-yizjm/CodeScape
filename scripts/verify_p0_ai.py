"""仅上传用户已明确授权的三个预置样例至 DeepSeek 官方 deepseek-flash。"""
import asyncio
import json
import uuid
from pathlib import Path

from codescape.analyzers import REPOSITORY, python_analysis
from codescape.context_tools import validate_explanations
from codescape.database import now
from codescape.ledger import ai_scope
from codescape.provider import PIPELINE_VERSION, Provider
from codescape.security import data_root

paths = ("input.py", "engine.py", "presentation.py")
files = {path: (REPOSITORY / "tests/fixtures/learning_project" / path).read_text(encoding="utf-8") for path in paths}
version = str(uuid.uuid4())
analysis = python_analysis(files, version)
provider = Provider(data_root())
settings = provider.settings()
if settings["base_url"] != "https://api.deepseek.com" or settings["model"] != "deepseek-flash":
    raise SystemExit("当前接收端或模型不在本次已有样例授权内；没有发起请求。")
with ai_scope(phase="architecture"):
    architecture, usage = asyncio.run(provider.architecture(analysis, files))
expected = {entity.id for entity in analysis.entities if entity.visible}
validate_explanations(architecture, analysis, expected, files, {path: [(1, len(text.splitlines()))] for path, text in files.items()})
explained = {note.entity_id for note in architecture.explanations}
leaf_id = next(entity.id for entity in analysis.entities if entity.name == "walk_syntax" and entity.visible)
leaf_note = next((note for note in architecture.explanations if note.entity_id == leaf_id), None)
leaf_words = (leaf_note.summary + leaf_note.details) if leaf_note else ""
report = {"verified_at": now(), "test_kind": "真实 DeepSeek 模型测试，与固定响应测试分开",
          "pipeline_version": PIPELINE_VERSION,
          "recipient": settings["base_url"], "model": settings["model"],
          "authorized_source_paths": ["tests/fixtures/learning_project/" + path for path in paths],
          "function_count": len(expected), "explained_count": len(explained), "missing_count": len(expected - explained),
          "version_id": version, "analysis": analysis.model_dump(),
          "leaf_count_wording_check": "通过，仍需人工核对其他职责" if "叶" in leaf_words and "所有后代节点" not in leaf_words else "未通过，叶级计数语义需复核",
          "source_references_validated": True, "usage": usage, "architecture": architecture.model_dump()}
output = Path("docs/reports/p0/deepseek函数讲解验证.json")
if output.exists():
    previous = output.with_name("deepseek函数讲解验证-首次.json")
    if previous.exists():
        previous = output.with_name(f"deepseek函数讲解验证-{uuid.uuid4().hex[:8]}.json")
    previous.write_bytes(output.read_bytes())
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: report[key] for key in ("test_kind", "function_count", "explained_count", "missing_count", "usage")}, ensure_ascii=False))
