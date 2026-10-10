from __future__ import annotations

import json

from pydantic import Field

from .models import Analysis, Architecture, Contract


class ConciseOverview(Contract):
    introduction: str = Field(min_length=1, max_length=160)
    capabilities: list[str] = Field(min_length=1, max_length=5)
    principle: str = Field(min_length=1, max_length=240)
    limitations: list[str] = Field(max_length=5)


def overview_context(architecture: Architecture, analysis: Analysis) -> str:
    """覆盖所有模块的精简材料；不发送源码、函数实现或用户目录。"""
    modules = architecture.modules
    # 全体模块均占有预算，避免大型仓库只读到前几批的 API 工具。
    summary_budget = min(200, max(16, 55_000 // max(len(modules), 1)))
    payload = {
        "scope": {"module_count": len(modules), "diagnostic_count": len(analysis.diagnostics),
                  "diagnostics_available": True,
                  "incomplete_module_count": sum(module.incomplete for module in modules)},
        "modules": [
            {"id": module.id, "parent_id": module.parent_id, "name": module.name[:80],
             "summary": module.summary[:summary_budget], "incomplete": module.incomplete}
            for module in modules
        ],
        "confirmed_collaboration": [],
        "batch_notes": [note[:160] for note in architecture.overview.limitations],
    }
    ownership = {entity_id: module.id for module in modules for entity_id in module.entity_ids}
    callers = {entity.scope_id: ownership.get(entity.id) for entity in analysis.entities if entity.visible}
    pairs: dict[tuple[str, str], int] = {}
    for call in analysis.calls:
        source, target = callers.get(call.caller_scope_id), ownership.get(call.target_id)
        if call.resolution == "confirmed" and source and target and source != target:
            pairs[source, target] = pairs.get((source, target), 0) + 1
    # 只用有证据的协作关系解释流程，不从目录顺序推断执行顺序。
    payload["confirmed_collaboration"] = [
        {"source": source, "target": target, "count": count}
        for (source, target), count in sorted(pairs.items(), key=lambda item: -item[1])[:120]
    ]
    return json.dumps(payload, ensure_ascii=False)
