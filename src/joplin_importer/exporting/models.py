"""Persisted models for the all-or-nothing managed export workflow."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EXPORT_PLAN_SCHEMA_VERSION = 1
EXPORT_APPROVAL_SCHEMA_VERSION = 1
EXPORT_RECEIPT_SCHEMA_VERSION = 2

ConflictPolicy = Literal["fail", "replace-managed"]
FolderKind = Literal["notebook", "section-group", "section"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportFolder(_Model):
    node_id: str
    parent_node_id: str | None = None
    title: str
    kind: FolderKind
    source_key: str


class ExportNote(_Model):
    action_id: str
    source_page_id: str
    parent_node_id: str
    title: str
    expected_body_sha256: str
    expected_semantic_sha256: str = ""
    expected_resource_hashes: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    page_order: int = 0


class ExportPlan(_Model):
    export_plan_schema_version: int = EXPORT_PLAN_SCHEMA_VERSION
    plan_id: str
    tool_version: str
    created_at_utc: str
    conflict_policy: ConflictPolicy = "fail"
    source_snapshot_id: str
    source_manifest_hash: str
    target_instance_fingerprint: str = ""
    content_mode: Literal["mixed-html-markdown"] = "mixed-html-markdown"
    folders: list[ExportFolder] = Field(default_factory=list)
    notes: list[ExportNote] = Field(default_factory=list)


class ExportApproval(_Model):
    export_approval_schema_version: int = EXPORT_APPROVAL_SCHEMA_VERSION
    export_plan_sha256: str
    operator: str = ""
    note: str = ""


class ExportDryRunReceipt(_Model):
    export_receipt_schema_version: int = EXPORT_RECEIPT_SCHEMA_VERSION
    created_at_utc: str
    plan_id: str
    export_plan_sha256: str
    approval_sha256: str
    target_instance_fingerprint: str
    live_folder_count: int
    live_note_count: int
    live_resource_count: int
    live_precondition_fingerprint: str = ""
    result: str = Field(default="ok", description="ok | failed")
    problems: list[str] = Field(default_factory=list)
    mutating_requests_sent: int = 0


class ExportApplyReceipt(_Model):
    export_receipt_schema_version: int = EXPORT_RECEIPT_SCHEMA_VERSION
    created_at_utc: str
    plan_id: str
    export_plan_sha256: str
    approval_sha256: str
    dry_run_receipt_sha256: str
    jex_backup_path: str = ""
    jex_backup_sha256: str = ""
    operator_confirmed_sync_complete: bool = False
    operator_confirmed_full_replace: bool = False
    operator_confirmed_empty_profile_no_backup: bool = False
    operator_confirmed_managed_profile_no_backup: bool = False
    folders_created: int = 0
    notes_created: int = 0
    resources_created: int = 0
    old_roots_trashed: int = 0


class ExportValidationIssue(_Model):
    kind: str
    source_page_id: str = ""
    joplin_note_ids: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""


class ExportValidationReport(_Model):
    export_validation_schema_version: int = 1
    created_at_utc: str
    plan_id: str
    source_snapshot_id: str
    target_snapshot_id: str
    strict_profile: bool = False
    result: str = Field(default="ok", description="ok | failed")
    planned_folders: int = 0
    planned_notes: int = 0
    target_folders: int = 0
    target_notes: int = 0
    validated_notes: int = 0
    issues: list[ExportValidationIssue] = Field(default_factory=list)
