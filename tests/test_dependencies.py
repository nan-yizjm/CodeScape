import pytest
from codescape.analyzers import python_analysis
from codescape.context_tools import ArchitectureReply
from codescape.dependencies import validate_primary_dependencies
from codescape.models import Architecture, CallJudgment, CodeReference, Module, Overview, PrimaryDependency
from codescape.provider import merge_architectures


def sample():
    analysis = python_analysis({"owned.py": "def leaf(): return 1\ndef caller(): return leaf()\n"}, "v")
    call = next(call for call in analysis.calls if call.resolution == "confirmed")
    arch = Architecture(modules=[Module(id="m", name="计算", summary="返回结果", code_paths=["owned.py"],
        entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
        overview=Overview(introduction="计算项目", capabilities=["计算"], principle="返回结果", limitations=[]),
        primary_dependencies=[PrimaryDependency(call_ids=[call.id], reason="主要入口通过计算取得返回结果。")])
    return analysis, call, arch


def test_primary_selection_round_trip_and_merge_keep_reason_and_real_evidence_ids():
    analysis, call, arch = sample()
    validate_primary_dependencies(arch, analysis, {call.id})
    reply = ArchitectureReply.model_validate(arch.model_dump()).architecture()
    assert reply.primary_dependencies == arch.primary_dependencies
    merged = merge_architectures([arch, arch], 2, analysis, {"owned.py"})
    assert len(merged.primary_dependencies) == 1
    assert merged.primary_dependencies[0].call_ids == [call.id]
    assert "主要入口" in merged.primary_dependencies[0].reason


@pytest.mark.parametrize("invalid", ["unknown", "unresolved", "indirect", "external", "outside_context", "duplicate", "blank_reason"])
def test_primary_selection_cannot_create_or_guess_a_dependency(invalid):
    analysis, call, arch = sample()
    allowed = {call.id}
    if invalid == "unknown":
        arch.primary_dependencies[0].call_ids = ["not-a-call"]
    elif invalid == "outside_context":
        allowed = set()
    elif invalid == "duplicate":
        arch.primary_dependencies[0].call_ids *= 2
    elif invalid == "blank_reason":
        arch.primary_dependencies[0].reason = "   "
    else:
        call.resolution = {"unresolved": "unresolved", "indirect": "excluded_indirect", "external": "external"}[invalid]
    with pytest.raises(ValueError, match="主要依赖"):
        validate_primary_dependencies(arch, analysis, allowed)


def test_target_conflict_also_removes_primary_selection():
    files = {"a.py": "def leaf(): return 1\n", "b.py": "def leaf(): return 2\n",
             "caller.py": "from a import leaf\ndef caller(): return leaf()\n"}
    analysis = python_analysis(files, "v")
    call = next(call for call in analysis.calls if call.resolution == "confirmed")
    target = next(entity for entity in analysis.entities if entity.name == "leaf" and entity.location.path == "b.py")
    arch = Architecture(modules=[Module(id="m", name="计算", summary="计算与输入", code_paths=list(files),
        entity_ids=[entity.id for entity in analysis.entities if entity.visible])],
        overview=Overview(introduction="计算", capabilities=["计算"], principle="返回结果", limitations=[]),
        primary_dependencies=[PrimaryDependency(call_ids=[call.id], reason="获取入口计算结果")],
        call_judgments=[CallJudgment(call_id=call.id, target_id=target.id, reason="固定冲突反例",
            evidence=[CodeReference(path=call.location.path,start_line=call.location.start_line,end_line=call.location.end_line),
                      CodeReference(path="b.py",start_line=1,end_line=1)])])
    with pytest.raises(ValueError, match="主要依赖"):
        validate_primary_dependencies(arch, analysis)
    merged = merge_architectures([arch, arch], 2, analysis, set(files))
    assert not merged.primary_dependencies
