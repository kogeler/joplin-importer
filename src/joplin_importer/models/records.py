"""Snapshot record models.

Records are serialized as canonical JSON inside snapshots. Anything that
participates in the deterministic inventory hash must be represented here;
volatile data (wall-clock durations, retry counts, host paths) belongs in
scan metadata instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    AuditRole,
    ContentFormat,
    NoteStatus,
    ResourceStatus,
    SourceBackend,
)

SNAPSHOT_SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def canonical_dict(self) -> dict:
        """JSON-compatible dict with deterministic content."""
        return self.model_dump(mode="json")


class ResourceRecord(_StrictModel):
    """One image/attachment/object referenced by a page or note."""

    source_reference: str = Field(
        description="Format-specific reference (e.g. ':/<id>' or XML callback ID)"
    )
    original_filename: str | None = None
    sanitized_filename: str | None = None
    media_type: str | None = None
    byte_length: int | None = None
    sha256: str | None = None
    stored_path: str | None = Field(
        default=None, description="Path inside the snapshot, if downloaded"
    )
    status: ResourceStatus = ResourceStatus.OK
    is_image: bool = False
    warnings: list[str] = Field(default_factory=list)


class ContentAnalysis(_StrictModel):
    """Fields shared by source pages and target notes."""

    raw_content_path: str | None = Field(default=None, description="Path inside the snapshot")
    raw_content_format: ContentFormat = ContentFormat.UNKNOWN
    raw_content_sha256: str | None = None
    semantic_model_path: str | None = None
    semantic_model_sha256: str | None = None
    normalizer_version: str | None = None
    normalized_text: str = ""
    normalized_text_sha256: str | None = None
    visible_text_length: int = 0
    image_count: int = 0
    attachment_count: int = 0
    link_targets: list[str] = Field(
        default_factory=list, description="Distinct link URLs in document order"
    )
    resource_hashes: list[str] = Field(default_factory=list)
    resources: list[ResourceRecord] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class PageRecord(ContentAnalysis):
    """One OneNote page captured from a source backend."""

    snapshot_schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_backend: SourceBackend
    audit_role: AuditRole
    source_page_id: str
    notebook_id: str = ""
    notebook_title: str = ""
    section_group_path: list[str] = Field(default_factory=list)
    section_id: str = ""
    section_title: str = ""
    page_title: str = ""
    page_level: int = 0
    page_order: int = 0
    created_at: str | None = Field(default=None, description="ISO 8601 UTC")
    updated_at: str | None = Field(default=None, description="ISO 8601 UTC")


class NoteRecord(ContentAnalysis):
    """One Joplin note captured through the Data API."""

    snapshot_schema_version: int = SNAPSHOT_SCHEMA_VERSION
    source_backend: SourceBackend = SourceBackend.JOPLIN_API
    audit_role: AuditRole = AuditRole.TARGET
    joplin_note_id: str
    parent_notebook_id: str = ""
    notebook_path: list[str] = Field(default_factory=list)
    title: str = ""
    markup_language: int | None = Field(
        default=None, description="1=Markdown, 2=HTML as reported by Joplin"
    )
    content_format: ContentFormat = ContentFormat.UNKNOWN
    has_body_html: bool = False
    status: NoteStatus = NoteStatus.NORMAL
    created_at: str | None = None
    updated_at: str | None = None
    user_created_at: str | None = None
    user_updated_at: str | None = None
    source_url: str = ""
    source_application: str = ""
    embedded_onenote_page_id: str | None = Field(
        default=None, description="OneNote page ID recovered from source_url or body marker"
    )


class ErrorRecord(_StrictModel):
    """One extractor error; stored in errors.jsonl (volatile, not hashed)."""

    scope: str = Field(description="notebook | section | page | resource | global")
    item_id: str = ""
    item_title: str = ""
    message: str
    exception_type: str = ""


class ScanMetadata(_StrictModel):
    """Volatile information about how a scan ran (not part of inventory hash)."""

    started_at_utc: str
    finished_at_utc: str
    host_os: str
    tool_version: str
    adapter: str
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    api_versions: dict[str, str] = Field(default_factory=dict)
    account_scope: str = Field(default="", description="Tenant/account for Graph scans")
    limitations: list[str] = Field(default_factory=list)
    error_count: int = 0


class Manifest(_StrictModel):
    """Snapshot manifest."""

    snapshot_schema_version: int = SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str
    tool_version: str
    source_backend: SourceBackend
    audit_role: AuditRole
    adapter_versions: dict[str, str] = Field(default_factory=dict)
    source_artifact_sha256: str | None = None
    scan_started_at_utc: str = ""
    scan_finished_at_utc: str = ""
    configuration: dict[str, str] = Field(
        default_factory=dict, description="Host-independent settings"
    )
    record_counts: dict[str, int] = Field(default_factory=dict)
    coverage_status: str = Field(default="complete", description="complete | partial")
    coverage_notes: list[str] = Field(default_factory=list)
    inventory_hash: str = Field(default="", description="Deterministic hash over canonical records")
    file_checksums: dict[str, str] = Field(
        default_factory=dict, description="snapshot-relative path -> sha256"
    )
