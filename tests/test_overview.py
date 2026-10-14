import asyncio
import json

import pytest
from codescape.analyzers import python_analysis
from codescape.models import Architecture, Module, Overview
from codescape.overview import overview_context
from codescape.provider import Provider, ProviderError


def fixture_architecture():
    analysis = python_analysis({"main.py": "def collect(): return 1\ndef publish(): return collect()\n"}, "v")
    architecture = Architecture(
        modules=[Module(id=entity.name, name=entity.name, summary="读取材料" if entity.name == "collect" else "发布结果",
                        entity_ids=[entity.id], code_paths=["main.py"]) for entity in analysis.entities],
        overview=Overview(introduction="旧版批次介绍", capabilities=["旧能力"], principle="旧原理",
                          limitations=["已有解析缺口"]),
    )
    return architecture, analysis


def test_overview_context_covers_all_modules_and_only_confirmed_evidence():
    architecture, analysis = fixture_architecture()
    text = overview_context(architecture, analysis)
    payload = json.loads(text)
    assert payload["scope"]["diagnostics_available"] and payload["scope"]["diagnostic_count"] == 0
    assert {item["id"] for item in payload["modules"]} == {"collect", "publish"}
    assert payload["confirmed_collaboration"] == [{"source": "publish", "target": "collect", "count": 1}]
    assert "def collect" not in text and "main.py" not in text and "旧版批次介绍" not in text
    analysis.calls[0].resolution = "unresolved"
    assert json.loads(overview_context(architecture, analysis))["confirmed_collaboration"] == []


def test_global_summary_preserves_limitations_and_does_not_change_architecture(tmp_path, monkeypatch):
    architecture, analysis = fixture_architecture()
    before = architecture.model_dump()

    async def fake(self, system, user, max_tokens):
        assert max_tokens == 8192 and "不能拼接批次说明" in system
        return {"introduction": "这是一个收集材料并发布结果的工具。", "capabilities": ["采集材料", "发布结果"],
                "principle": "将材料收集后整理为可阅读结果。", "limitations": ["无法确认完整运行效果"]}, {"total_tokens": 12}

    monkeypatch.setattr(Provider, "request", fake)
    overview, usage = asyncio.run(Provider(tmp_path).project_overview(architecture, analysis))
    assert overview.synthesized and "已有解析缺口" in overview.analysis_notes
    assert overview.limitations == ["无法确认完整运行效果"]
    assert usage == {"total_tokens": 12} and architecture.model_dump() == before


@pytest.mark.parametrize("change", [{"introduction": "本批源码提供工具"}, {"introduction": "长" * 161},
                                  {"capabilities": ["能力"] * 6}, {"capabilities": ["长" * 49]},
                                  {"principle": "长" * 241}])
def test_invalid_global_summary_is_rejected_without_changing_old_result(tmp_path, monkeypatch, change):
    architecture, analysis = fixture_architecture()
    before = architecture.model_dump()

    async def fake(self, system, user, max_tokens):
        return {"introduction": "这是一个工具", "capabilities": ["采集材料"], "principle": "整理材料",
                "limitations": [], **change}, {}

    monkeypatch.setattr(Provider, "request", fake)
    with pytest.raises(ProviderError, match="全局概览"):
        asyncio.run(Provider(tmp_path).project_overview(architecture, analysis))
    assert architecture.model_dump() == before


def test_summary_truncation_does_not_ask_to_reduce_source_scope(tmp_path, monkeypatch):
    architecture, analysis = fixture_architecture()

    async def fake(self, system, user, max_tokens):
        raise ProviderError("模型输出被截断，请缩小分析批次。")

    monkeypatch.setattr(Provider, "request", fake)
    with pytest.raises(ProviderError, match="原有结果已保留，请重试概览归纳"):
        asyncio.run(Provider(tmp_path).project_overview(architecture, analysis))
