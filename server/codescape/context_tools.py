from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field

from .models import (
    Architecture,
    CallJudgment,
    CodeReference,
    Contract,
    FunctionExplanation,
    Module,
    Overview,
    PrimaryDependency,
)
from .validation import SourceContractError


class ReadRequest(Contract):
    kind: Literal["code", "entity", "confirmed_relations", "diagnostics"]
    reference: CodeReference | None = None
    ids: list[str] = Field(default_factory=list, max_length=20)


class ArchitectureReply(Contract):
    modules: list[Module] = Field(default_factory=list)
    overview: Overview | None = None
    explanations: list[FunctionExplanation] = Field(default_factory=list)
    call_judgments: list[CallJudgment] = Field(default_factory=list)
    primary_dependencies: list[PrimaryDependency] = Field(default_factory=list)
    read_requests: list[ReadRequest] = Field(default_factory=list, max_length=4)

    def architecture(self):
        if self.overview is None:
            raise ValueError("缺少项目概览")
        return Architecture(modules=self.modules, overview=self.overview, explanations=self.explanations,
                            call_judgments=self.call_judgments, primary_dependencies=self.primary_dependencies)


class SnapshotTools:
    """Read only already-authorized in-memory snapshot data; never resolve a filesystem path."""
    def __init__(self, analysis, files):
        self.analysis, self.files = analysis, files
        self.total_chars = 0
        self.read_ranges: dict[str, list[tuple[int, int]]] = {}

    def read(self, requests):
        values, size, ranges = [], 0, {}
        entities = {entity.id: entity for entity in self.analysis.entities}
        calls = {call.id: call for call in self.analysis.calls if call.resolution == "confirmed"}
        for request in requests:
            if request.kind == "code":
                ref = request.reference
                if ref is None or request.ids:
                    raise ValueError("补读参数错误")
                path = PurePosixPath(ref.path)
                if (ref.path not in self.files or path.is_absolute() or ".." in path.parts
                        or "\\" in ref.path or ":" in ref.path):
                    raise ValueError("补读超出当前授权快照")
                lines = self.files[ref.path].splitlines(keepends=True)
                if ref.end_line > len(lines) or ref.end_line - ref.start_line >= 200:
                    raise ValueError("补读范围超过保护上限")
                text = "".join(lines[ref.start_line - 1:ref.end_line])
                size += len(text)
                if len(text) > 20_000:
                    raise ValueError("补读片段超过保护上限")
                ranges.setdefault(ref.path, []).append((ref.start_line, ref.end_line))
                values.append({"kind": "code", **ref.model_dump(), "text": text})
            else:
                if request.reference:
                    raise ValueError("补读参数错误")
                if request.kind == "entity":
                    if not request.ids or any(identifier not in entities for identifier in request.ids):
                        raise ValueError("实体不属于当前快照")
                    values.append({"kind": request.kind, "items": [entities[item].model_dump() for item in request.ids]})
                elif request.kind == "confirmed_relations":
                    if not request.ids or any(identifier not in calls for identifier in request.ids):
                        raise ValueError("调用不属于当前确认集")
                    values.append({"kind": request.kind, "items": [calls[item].model_dump() for item in request.ids]})
                else:
                    if request.ids:
                        raise ValueError("诊断补读不接受外部标识")
                    values.append({"kind": request.kind, "items": [item.model_dump() for item in self.analysis.diagnostics[:100]]})
        import json
        size = max(size, len(json.dumps(values, ensure_ascii=False)))
        if self.total_chars + size > 80_000:
            raise ValueError("补读总量超过保护上限")
        self.total_chars += size
        for path, items in ranges.items():
            self.read_ranges.setdefault(path, []).extend(items)
        return values


def validate_explanations(architecture, analysis, expected, files, ranges):
    entities = {entity.id: entity for entity in analysis.entities if entity.id in expected}
    seen = set()
    for note in architecture.explanations:
        if note.entity_id not in entities or note.entity_id in seen or not note.summary.strip() or not note.details.strip():
            raise ValueError("函数讲解引用错误")
        seen.add(note.entity_id)
        entity = entities[note.entity_id]
        own_evidence = False
        for ref in note.evidence:
            lines = files.get(ref.path, "").splitlines()
            if ref.end_line - ref.start_line >= 200:
                raise SourceContractError("单条讲解源码引用最多200行，请拆为多个已提供范围内的引用", entity.id,
                    (entity.location.start_line, entity.location.end_line), ranges.get(entity.location.path, []))
            if (ref.end_line > len(lines)
                    or not any(start <= ref.start_line <= ref.end_line <= end for start, end in ranges.get(ref.path, []))):
                raise SourceContractError("讲解引用未提供的源码位置", entity.id,
                    (entity.location.start_line, entity.location.end_line), ranges.get(entity.location.path, []))
            own_evidence |= ref.path == entity.location.path and entity.location.start_line <= ref.start_line <= ref.end_line <= entity.location.end_line
        if not own_evidence:
            raise SourceContractError("函数讲解缺少实现自身的依据", entity.id,
                (entity.location.start_line, entity.location.end_line), ranges.get(entity.location.path, []))
