"""Match result and finding models shared by matching, reporting, and repair."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import CauseClass, EvidenceClass, FindingKind, MatchConfidence


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_page_id: str
    joplin_note_id: str | None = None
    confidence: MatchConfidence
    stage: str = Field(description="which rule/stage produced the match")
    score: float | None = None
    runner_up_margin: float | None = None
    features: dict[str, float | None] = Field(default_factory=dict)
    weights: dict[str, float] = Field(default_factory=dict)
    threshold_version: str = ""
    explanation: list[str] = Field(default_factory=list)
    # denormalized context for reports
    source_title: str = ""
    source_path: list[str] = Field(default_factory=list)
    target_title: str = ""
    target_path: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FindingKind
    evidence: EvidenceClass
    cause: CauseClass
    source_page_id: str | None = None
    joplin_note_id: str | None = None
    title: str = ""
    path: list[str] = Field(default_factory=list)
    explanation: str = ""
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class AuditSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_version: str = ""
    threshold_version: str = ""
    source_snapshot_id: str = ""
    target_snapshot_id: str = ""
    source_manifest_hash: str = ""
    target_manifest_hash: str = ""
    source_pages: int = 0
    target_notes: int = 0
    matches_by_confidence: dict[str, int] = Field(default_factory=dict)
    findings_by_kind: dict[str, int] = Field(default_factory=dict)
    findings_by_evidence: dict[str, int] = Field(default_factory=dict)
    findings_by_cause: dict[str, int] = Field(default_factory=dict)


class AuditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: AuditSummary
    matches: list[MatchResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
