from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Contract):
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)


class Entity(Contract):
    id: str
    name: str
    qualified_name: str
    signature: str
    signature_key: str | None = None
    language: str
    location: Location
    scope_id: str
    visible: bool = True
    hidden_reason: str | None = None
    implementation_hash: str
    condition: str | None = None
    explanation: str | None = None
    explanation_details: str | None = None
    explanation_evidence: list[dict] = Field(default_factory=list)
    unresolved_count: int = 0


class Scope(Contract):
    id: str
    kind: str
    name: str
    location: Location
    parent_id: str | None = None


class Call(Contract):
    id: str
    caller_scope_id: str
    target_id: str | None = None
    expression: str
    location: Location
    resolution: Literal["confirmed", "unresolved", "excluded_indirect", "external"] = "unresolved"
    spelling_location: Location | None = None
    expansion_location: Location | None = None
    origin: Literal["static", "ai_source_judgment"] = "static"
    reason: str = ""
    target_name: str | None = None
    judgment_evidence: list[dict] = Field(default_factory=list)


class Diagnostic(Contract):
    category: str
    message: str
    path: str | None = None
    line: int | None = None


class Analysis(Contract):
    entities: list[Entity] = Field(default_factory=list)
    scopes: list[Scope] = Field(default_factory=list)
    calls: list[Call] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    tools: dict[str, str] = Field(default_factory=dict)
    file_hashes: dict[str, str] = Field(default_factory=dict)
    readable_paths: list[str] = Field(default_factory=list)


class Module(Contract):
    id: str
    parent_id: str | None = None
    name: str
    summary: str
    entity_ids: list[str] = Field(default_factory=list)
    code_paths: list[str] = Field(default_factory=list)
    incomplete: bool = False


class Overview(Contract):
    introduction: str
    capabilities: list[str]
    principle: str
    limitations: list[str]
    synthesized: bool = False
    analysis_notes: list[str] = Field(default_factory=list)


class CodeReference(Contract):
    path: str
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_line < self.start_line:
            raise ValueError("源码范围顺序错误")
        return self


class FunctionExplanation(Contract):
    entity_id: str
    summary: str = Field(min_length=1, max_length=160)
    details: str = Field(min_length=1, max_length=1200)
    evidence: list[CodeReference] = Field(min_length=1, max_length=6)


class CallJudgment(Contract):
    call_id: str
    target_id: str
    reason: str = Field(min_length=1, max_length=800)
    evidence: list[CodeReference] = Field(min_length=2, max_length=8)


class PrimaryDependency(Contract):
    call_ids: list[str] = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=800)


class Architecture(Contract):
    modules: list[Module]
    overview: Overview
    explanations: list[FunctionExplanation] = Field(default_factory=list)
    call_judgments: list[CallJudgment] = Field(default_factory=list)
    primary_dependencies: list[PrimaryDependency] = Field(default_factory=list)


class ImportRequest(Contract):
    path: str | None = Field(default=None, max_length=4096)
    url: str | None = Field(default=None, max_length=2048)
    preview_id: str | None = None
    excludes: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)


class ProjectDeleteRequest(Contract):
    confirm: bool = Field(strict=True)


class AISettings(Contract):
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    key: str | None = None


class LearningRequest(Contract):
    version_id: str
    state: Literal["not_started", "in_progress", "mastered"]
    expected_revision: int = Field(ge=0)


class SavedViewport(Contract):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    zoom: float = Field(gt=0, le=10, allow_inf_nan=False)


class SavedScroll(Contract):
    top: float = Field(ge=0, le=1_000_000_000, allow_inf_nan=False)
    left: float = Field(default=0, ge=0, le=1_000_000_000, allow_inf_nan=False)
    line: int | None = Field(default=None, ge=1)


class SavedPanel(Contract):
    kind: Literal["overview", "module", "function", "relation", "source", "diagnostics"]
    module_id: str | None = None
    entity_id: str | None = None
    relation_id: str | None = None
    from_id: str | None = None
    to_id: str | None = None
    call_ids: list[str] = Field(default_factory=list, max_length=50_000)
    location: Location | None = None
    scroll: SavedScroll | None = None

    @model_validator(mode="after")
    def references(self):
        if ((self.kind == "module" and not self.module_id)
                or (self.kind == "function" and not self.entity_id)
                or (self.kind == "source" and not self.location)
                or (self.kind == "relation" and not all((self.relation_id, self.from_id, self.to_id)))):
            raise ValueError("探索面板缺少必要引用")
        return self


class NavigationOrigin(Contract):
    focus: str | None = None
    expanded: str | None = None
    siblings: bool = False


class SessionRequest(Contract):
    version_id: str
    focus_id: str | None = None
    panel: None = None
    viewport: dict[str, SavedViewport] | None = Field(default=None, max_length=5000)
    expected_revision: int = Field(default=0, ge=0)
    expanded_id: str | None = None
    show_other_modules: bool = False
    dependency_mode: Literal["primary", "all"] = "primary"
    function_origin: NavigationOrigin | None = None
    panels: list[SavedPanel] = Field(default_factory=list, max_length=20)
    expanded_evidence: dict[str, bool] = Field(default_factory=dict, max_length=5000)
    panel_width: int = Field(default=420, ge=320, le=650)
