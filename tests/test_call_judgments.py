import asyncio

import pytest
from codescape.analyzers import python_analysis
from codescape.call_judgments import reconcile_judgments, validate_judgments
from codescape.models import Architecture, CallJudgment, Module, Overview
from codescape.provider import Provider, merge_architectures
from codescape.sessions import validate_session


def fixture():
    files = {"codec.py": "class Codec:\n    @staticmethod\n    def encode(value):\n        return str(value)\ndef render():\n    return Codec.encode(1)\n",
             "display.py": "def present():\n    return 1\n"}
    analysis = python_analysis(files, "version")
    call = next(call for call in analysis.calls if call.expression == "Codec.encode")
    target = next(entity for entity in analysis.entities if entity.name == "encode")
    architecture = Architecture(modules=[Module(id=entity.id, name=entity.name, summary="应用自有测试模块",
        entity_ids=[entity.id], code_paths=[entity.location.path]) for entity in analysis.entities if entity.visible],
        overview=Overview(introduction="固定响应关系验证", capabilities=["验证依据"], principle="只读取源码", limitations=[]))
    architecture.call_judgments = [CallJudgment(call_id=call.id, target_id=target.id,
        reason="codec.py 明确定义 Codec，render 调用 Codec.encode；Codec 定义的静态方法没有实例分派，引用展示完整绑定和目标实现。",
        evidence=[{"path": "codec.py", "start_line": 5, "end_line": 6}, {"path": "codec.py", "start_line": 1, "end_line": 4}])]
    ranges = {path: [(1, len(text.splitlines()))] for path, text in files.items()}
    return files, analysis, call, architecture, ranges


def test_source_judgment_preserves_real_scope_and_static_input():
    files, analysis, call, architecture, ranges = fixture()
    assert call.resolution == "unresolved"
    validate_judgments(architecture, analysis, {call.id}, files, ranges)
    result = reconcile_judgments(analysis, architecture)
    admitted = next(item for item in result.calls if item.id == call.id)
    assert admitted.resolution == "confirmed" and admitted.origin == "ai_source_judgment"
    assert admitted.caller_scope_id == call.caller_scope_id and admitted.location == call.location
    assert admitted.judgment_evidence[0]["reason"] == architecture.call_judgments[0].reason
    assert call.resolution == "unresolved" and call.target_id is None
    assert next(entity for entity in result.entities if entity.name == "render").unresolved_count == 0


@pytest.mark.parametrize('missing', ['call', 'target'])
def test_range_feedback_identifies_the_missing_side(missing):
    from codescape.validation import SourceContractError
    files, analysis, call, architecture, ranges = fixture()
    target=next(e for e in analysis.entities if e.id==architecture.call_judgments[0].target_id)
    evidence=architecture.call_judgments[0].evidence
    if missing=='call':
        evidence[0].start_line=evidence[0].end_line=5
    else:
        evidence[1].end_line=3
    with pytest.raises(SourceContractError) as caught:
        validate_judgments(architecture,analysis,{call.id},files,ranges)
    location=call.location if missing=='call' else target.location
    assert caught.value.identifier==(call.id if missing=='call' else target.id)
    assert caught.value.implementation_lines==(location.start_line,location.end_line)


@pytest.mark.parametrize("change,expected", [("unknown_call", "引用错误"), ("unknown_target", "引用错误"),
    ("indirect", "间接调用"), ("external", "范围外"), ("dynamic", "动态语义"), ("missing_code", "未提供"),
    ("partial_target", "完整目标"), ("wrong_name", "名称不对应"), ("condition", "条件编译")])
def test_invalid_or_ambiguous_judgment_cannot_publish(change, expected):
    files, analysis, call, architecture, ranges = fixture()
    call.resolution = "unresolved"
    judgment = architecture.call_judgments[0]
    if change == "unknown_call":
        judgment.call_id = "imaginary"
    elif change == "unknown_target":
        judgment.target_id = "imaginary"
    elif change in {"indirect", "external"}:
        call.resolution = "excluded_indirect" if change == "indirect" else "external"
    elif change == "dynamic":
        call.reason = "成员动态分派或装饰器语义未确认。"
    elif change == "missing_code":
        ranges.pop("codec.py")
    elif change == "partial_target":
        judgment.evidence[1].end_line = 3
    elif change == "wrong_name":
        call.expression = "Codec.decode"
    else:
        next(entity for entity in analysis.entities if entity.name == "encode").condition = "defined(A)"
    with pytest.raises(ValueError, match=expected):
        validate_judgments(architecture, analysis, {call.id}, files, ranges)


def test_static_ai_disagreement_removes_edge_and_keeps_both_targets():
    files, analysis, call, architecture, ranges = fixture()
    other = next(entity for entity in analysis.entities if entity.name == "encode").model_copy(deep=True)
    other.id, other.scope_id, other.qualified_name = "second-target", "second-scope", "SecondCodec.encode"
    # A second real implementation location, with the same callable identifier.
    other.location.path = "second.py"
    files["second.py"] = files["codec.py"].replace("Codec", "SecondCodec")
    ranges["second.py"] = [(1, 4)]
    analysis.entities.append(other)
    call.resolution, call.target_id, call.target_name = "confirmed", other.id, other.qualified_name
    validate_judgments(architecture, analysis, {call.id}, files, ranges)
    result = reconcile_judgments(analysis, architecture)
    conflicted = next(item for item in result.calls if item.id == call.id)
    assert conflicted.resolution == "unresolved" and conflicted.target_id is None
    assert "codec.py" in conflicted.reason and "second.py" in conflicted.reason
    assert conflicted.judgment_evidence and any(item.category == "CALL_TARGET_CONFLICT" for item in result.diagnostics)
    assert next(entity for entity in result.entities if entity.name == "render").unresolved_count == 1
    assert call.resolution == "confirmed" and call.target_id == other.id


def test_conditional_repair_identifies_optional_judgment_without_admitting_it(tmp_path, monkeypatch):
    from codescape.provider import validation_feedback
    from codescape.validation import SourceContractError

    files, analysis, call, architecture, ranges = fixture()
    target = next(entity for entity in analysis.entities if entity.name == 'encode')
    target.condition = 'defined(UNKNOWN_BUILD_OPTION)'
    with pytest.raises(SourceContractError) as caught:
        validate_judgments(architecture, analysis, {call.id}, files, ranges)
    feedback = validation_feedback(caught.value)
    assert call.id in feedback and target.id in feedback and '移除该可选判断' in feedback
    assert 'UNKNOWN_BUILD_OPTION' not in feedback
    assert call.resolution == 'unresolved' and call.target_id is None

    provider = Provider(tmp_path)
    requests = []
    repaired = architecture.model_copy(deep=True)
    repaired.call_judgments = []

    async def fixed(system, payload, **kwargs):
        requests.append(system)
        if len(requests) == 1:
            return architecture.model_dump(), {}
        assert call.id in system and target.id in system and '移除该可选判断' in system
        return repaired.model_dump(), {}

    monkeypatch.setattr(provider, 'request', fixed)
    result, _ = asyncio.run(provider.architecture(analysis, files))
    assert len(requests) == 2 and not result.call_judgments
    assert result.modules == architecture.modules
    retained = reconcile_judgments(analysis, result)
    assert next(item for item in retained.calls if item.id == call.id).resolution == 'unresolved'


def test_ai_agreement_does_not_relabel_static_confirmation():
    files, analysis, call, architecture, ranges = fixture()
    target = next(entity for entity in analysis.entities if entity.name == "encode")
    call.resolution, call.target_id, call.target_name = "confirmed", target.id, target.qualified_name
    result = reconcile_judgments(analysis, architecture)
    agreed = next(item for item in result.calls if item.id == call.id)
    assert agreed.origin == "static" and agreed.target_id == target.id and agreed.judgment_evidence
    assert not any(item.category == "CALL_TARGET_CONFLICT" for item in result.diagnostics)


def test_hidden_scope_is_never_reattributed_to_visible_parent():
    files = {"a.py": "class Codec:\n    @staticmethod\n    def encode(x): return x\ndef outer():\n    return [Codec.encode(x) for x in (1, 2)]\n"}
    analysis = python_analysis(files, "v")
    call = next(item for item in analysis.calls if item.expression == "Codec.encode")
    target = next(entity for entity in analysis.entities if entity.name == "encode")
    architecture = Architecture(modules=[], overview=Overview(introduction="固定验证", capabilities=[], principle="不执行源码", limitations=[]),
        call_judgments=[CallJudgment(call_id=call.id, target_id=target.id, reason="类静态方法，列表推导式作用域中直接以类名调用。",
        evidence=[{"path": "a.py", "start_line": 1, "end_line": 3}, {"path": "a.py", "start_line": 4, "end_line": 5}])])
    validate_judgments(architecture, analysis, {call.id}, files, {"a.py": [(1, 5)]})
    result = reconcile_judgments(analysis, architecture)
    actual = next(item for item in result.calls if item.id == call.id)
    assert actual.caller_scope_id == call.caller_scope_id
    assert actual.caller_scope_id != next(entity for entity in analysis.entities if entity.name == "outer").scope_id


def test_provider_keeps_judgments_through_batch_reply_and_merge(tmp_path, monkeypatch):
    files, analysis, call, architecture, ranges = fixture()
    call.resolution = "unresolved"
    provider = Provider(tmp_path)
    async def fixed(*args, **kwargs):
        return architecture.model_dump(), {"total_tokens": 7}
    monkeypatch.setattr(provider, "request", fixed)
    result, _ = asyncio.run(provider.architecture(analysis, files))
    assert result.call_judgments == architecture.call_judgments
    merged = merge_architectures([result, result], 2, analysis, set(files))
    assert merged.call_judgments == architecture.call_judgments


def test_imported_class_member_is_not_mistaken_for_out_of_scope_class_name():
    files = {"a.py": "class Codec:\n    def encode(value): return value\n",
        "b.py": "from a import Codec\ndef render(): return Codec.encode(1)\n"}
    analysis = python_analysis(files, "v")
    call = next(item for item in analysis.calls if item.expression == "Codec.encode")
    assert call.resolution == "confirmed"
    assert call.target_id == next(entity for entity in analysis.entities if entity.name == "encode").id
    value = Architecture(modules=[], overview=Overview(introduction="固定验证", capabilities=[], principle="只读", limitations=[]),
        call_judgments=[CallJudgment(call_id=call.id, target_id=call.target_id, reason="明确的导入绑定与类内实现一致。",
            evidence=[{"path": "a.py", "start_line": 1, "end_line": 2}, {"path": "b.py", "start_line": 1, "end_line": 2}])])
    # A static reason mentioning the absence of decorator rewriting is not a
    # dynamic warning, and must not block agreement or conflict validation.
    validate_judgments(value, analysis, {call.id}, files, {path: [(1, 2)] for path in files})


def test_target_metadata_is_not_enough_until_implementation_is_read(tmp_path, monkeypatch):
    files, analysis, call, architecture, ranges = fixture()
    provider = Provider(tmp_path)
    render = next(entity for entity in analysis.entities if entity.name == "render")
    architecture.modules = [module for module in architecture.modules if render.id in module.entity_ids]
    context, expected = provider._context(analysis, {"codec.py": "def render():\n    return Codec.encode(1)\n"}, {"codec.py": (5, 6)})
    payloads = []
    async def fixed(system, payload, **kwargs):
        import json
        payloads.append(json.loads(payload))
        if len(payloads) == 1:
            assert "candidate_entities" in payloads[-1] and not payloads[-1].get("supplemental_reads")
            return {"read_requests": [{"kind": "code", "reference": {"path": "codec.py", "start_line": 1, "end_line": 4}}]}, {}
        assert payloads[-1]["supplemental_reads"][0]["text"].startswith("class Codec:")
        return architecture.model_dump(), {}
    monkeypatch.setattr(provider, "request", fixed)
    result, _ = asyncio.run(provider._read_batch("固定验证", context, Architecture.model_validate, analysis, files, {"codec.py": (5, 6)}))
    assert result.call_judgments == architecture.call_judgments and len(payloads) == 2


def test_session_accepts_saved_ai_evidence_and_rejects_conflicted_calls():
    files, analysis, call, architecture, ranges = fixture()
    project = {"result": analysis.model_dump(), "architecture": architecture.model_dump(),
               "snapshot": {"files": {path: "fixture-hash" for path in files}}}
    session = {"panels": [{"call_ids": [call.id]}]}
    validate_session(session, project)
    other = next(entity for entity in analysis.entities if entity.name == "encode").model_copy(deep=True)
    other.id = "another-real-target"
    analysis.entities.append(other)
    call.resolution, call.target_id = "confirmed", other.id
    project["result"] = analysis.model_dump()
    with pytest.raises(ValueError, match="外部调用"):
        validate_session(session, project)
