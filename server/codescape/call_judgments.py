"""Admit source-grounded AI judgments without replacing scopes or indirect calls."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import Diagnostic


def callable_name(expression):
    # Calls/indexing/optional chaining in the callee itself are dynamic. Do not
    # interpret arbitrary expressions as a concrete named implementation.
    if not re.fullmatch(r"[\w$]+(?:(?:\.|::)[\w$]+)*", expression, flags=re.UNICODE):
        return None
    return re.split(r"\.|::", expression)[-1]


def validate_judgments(architecture, analysis, allowed_calls, files, supplied):
    calls = {call.id: call for call in analysis.calls}
    entities = {entity.id: entity for entity in analysis.entities}
    scopes = {scope.id: scope for scope in analysis.scopes}
    seen = set()
    for judgment in architecture.call_judgments:
        call, target = calls.get(judgment.call_id), entities.get(judgment.target_id)
        if (not call or not target or call.id not in allowed_calls or call.id in seen
                or call.caller_scope_id not in scopes or not judgment.reason.strip()):
            raise ValueError("调用判断引用错误或重复")
        seen.add(call.id)
        if call.resolution not in {"confirmed", "unresolved"}:
            raise ValueError("间接调用或范围外目标不能升级为直接调用")
        if call.resolution == "unresolved" and any(word in call.reason for word in ("动态分派", "装饰器", "虚调用")):
            raise ValueError("静态已知的动态语义不能升级为直接调用")
        if callable_name(call.expression) != re.split(r"\.|::", target.name)[-1]:
            raise ValueError("调用表达式与目标实现名称不对应")
        caller = next((entity for entity in analysis.entities if entity.scope_id == call.caller_scope_id), None)
        source_suffix = PurePosixPath(call.location.path).suffix.lower()
        target_suffix = PurePosixPath(target.location.path).suffix.lower()
        def family(suffix):
            return "python" if suffix == ".py" else "typescript" if suffix in {".ts", ".tsx", ".mts", ".cts"} else "native"
        if family(source_suffix) != family(target_suffix):
            raise ValueError("跨语言接口不能仅按同名确认为直接函数调用")
        if target.condition and (not caller or target.condition != caller.condition):
            raise ValueError("条件编译适用性未确认，不能升级调用")
        for ref in judgment.evidence:
            if (ref.path not in files or ref.end_line > len(files[ref.path].splitlines())
                    or not any(start <= ref.start_line <= ref.end_line <= end for start, end in supplied.get(ref.path, []))):
                raise ValueError("调用判断依据未提供或超出快照")
        def contains(location):
            cursor = location.start_line
            for start, end in sorted((ref.start_line, ref.end_line) for ref in judgment.evidence if ref.path == location.path):
                if end < cursor:
                    continue
                if start > cursor:
                    return False
                cursor = end + 1
                if cursor > location.end_line:
                    return True
            return False
        if not contains(call.location) or not contains(target.location):
            raise ValueError("调用判断必须提供具体调用位置和完整目标实现")


def reconcile_judgments(analysis, architecture):
    """Keep the immutable static input; conflicting targets become unresolved."""
    result = analysis.model_copy(deep=True)
    entities = {entity.id: entity for entity in result.entities}
    groups = {}
    for judgment in architecture.call_judgments:
        groups.setdefault(judgment.call_id, []).append(judgment)
    for call in result.calls:
        judgments = groups.get(call.id, [])
        if not judgments:
            continue
        targets = {item.target_id for item in judgments}
        # Rejection here is defensive; publication performs full range validation.
        if not targets.issubset(entities) or call.resolution not in {"confirmed", "unresolved"}:
            continue
        call.judgment_evidence = [item.model_dump() for item in judgments]
        if call.resolution == "confirmed" and call.target_id:
            targets.add(call.target_id)
        if len(targets) != 1:
            names = "、".join(sorted(f"{entities[identifier].qualified_name}（{entities[identifier].location.path}:"
                                      f"{entities[identifier].location.start_line}）" for identifier in targets))
            call.resolution = "unresolved"
            call.target_id = None
            call.target_name = None
            call.reason = f"目标判断冲突：静态或 AI 依据指向不同实现（{names}）；保留未解析，不绘制确定边。"
            result.diagnostics.append(Diagnostic(category="CALL_TARGET_CONFLICT", message=call.reason,
                path=call.location.path, line=call.location.start_line))
        elif call.resolution == "unresolved":
            target = entities[next(iter(targets))]
            call.target_id, call.target_name = target.id, target.qualified_name
            call.resolution, call.origin = "confirmed", "ai_source_judgment"
            call.reason = judgments[0].reason
        # Agreement never turns a static confirmation into an AI-only result.
    for entity in result.entities:
        entity.unresolved_count = sum(call.caller_scope_id == entity.scope_id
            and call.resolution in {"unresolved", "excluded_indirect"} for call in result.calls)
    return result
