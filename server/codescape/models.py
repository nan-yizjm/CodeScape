from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    language: str
    location: Location
    scope_id: str
    visible: bool = True
    hidden_reason: str | None = None
    implementation_hash: str
    condition: str | None = None
    explanation: str | None = None
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
    origin: Literal["static", "ai_source_judgment"] = "static"
    reason: str = ""
    target_name: str | None = None


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


class Architecture(Contract):
    modules: list[Module]
    overview: Overview


class ImportRequest(Contract):
    path: str | None = Field(default=None, max_length=4096)
    url: str | None = Field(default=None, max_length=2048)
    preview_id: str | None = None
    excludes: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)


class AISettings(Contract):
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    key: str | None = None


class LearningRequest(Contract):
    version_id: str
    state: Literal["not_started", "in_progress", "mastered"]
    expected_revision: int = Field(ge=0)


class SessionRequest(Contract):
    version_id: str
    focus_id: str | None = None
    panel: dict | None = None
    viewport: dict | None = None
