"""Repair plan, approval, receipt, and journal models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..models.enums import MatchConfidence, RepairActionType
from ..models.hashing import sha256_file

REPAIR_PLAN_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1
RECEIPT_SCHEMA_VERSION = 1

RECOVERY_NOTEBOOK = "_OneNote Recovery"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preconditions(_Model):
    """Checked against the live target immediately before executing an action."""

    #: for update actions: the current note body hash must still match
    existing_note_body_sha256: str | None = None
    #: the destination notebook name must resolve unambiguously
    destination_must_be_unambiguous: bool = True
    #: no note carrying this idempotency key may already exist
    idempotency_key_absent: bool = True


class RepairAction(_Model):
    action_id: str
    idempotency_key: str
    source_page_id: str
    existing_joplin_note_id: str | None = None
    action: RepairActionType
    confidence: MatchConfidence | None = None
    reason: str = ""
    destination_notebook: str = RECOVERY_NOTEBOOK
    expected_title: str = ""
    intended_content_format: str = Field(default="html", description="html | markdown")
    expected_body_sha256: str = ""
    expected_semantic_sha256: str = ""
    expected_resource_hashes: list[str] = Field(default_factory=list)
    preconditions: Preconditions = Field(default_factory=Preconditions)


class RepairPlan(_Model):
    repair_plan_schema_version: int = REPAIR_PLAN_SCHEMA_VERSION
    plan_id: str
    tool_version: str
    created_at_utc: str
    mode: str = "create-only"
    source_snapshot_id: str
    target_snapshot_id: str
    source_manifest_hash: str
    target_manifest_hash: str
    #: identifies the Joplin instance the plan was computed against
    target_instance_fingerprint: str
    destination_notebook: str = RECOVERY_NOTEBOOK
    actions: list[RepairAction] = Field(default_factory=list)


class ApprovalFile(_Model):
    approval_schema_version: int = APPROVAL_SCHEMA_VERSION
    #: SHA-256 of the exact repair-plan file bytes this approval refers to
    repair_plan_sha256: str
    approved_action_ids: list[str] = Field(default_factory=list)
    operator: str = ""
    note: str = ""


class DryRunReceipt(_Model):
    receipt_schema_version: int = RECEIPT_SCHEMA_VERSION
    created_at_utc: str
    plan_id: str
    repair_plan_sha256: str
    approval_sha256: str
    selected_action_ids: list[str] = Field(default_factory=list)
    target_instance_fingerprint: str
    joplin_api_version: str = ""
    #: hash over the live state relevant to preconditions at dry-run time
    live_precondition_fingerprint: str = ""
    result: str = Field(default="ok", description="ok | failed")
    problems: list[str] = Field(default_factory=list)
    #: proof: mutating requests actually sent by the transport during dry-run
    mutating_requests_sent: int = 0


class JournalEntry(_Model):
    """Append-only apply journal record (no secrets, no bodies)."""

    at_utc: str
    action_id: str
    idempotency_key: str
    step: str  # e.g. resolve-destination | create-resource | create-note | skip
    status: str  # ok | skipped | failed
    joplin_id: str = ""
    detail: str = ""


class ApplyReceipt(_Model):
    receipt_schema_version: int = RECEIPT_SCHEMA_VERSION
    created_at_utc: str
    plan_id: str
    repair_plan_sha256: str
    approval_sha256: str
    dry_run_receipt_sha256: str
    #: operator-supplied safety acknowledgements
    jex_backup_path: str = ""
    jex_backup_sha256: str = ""
    operator_confirmed_sync_complete: bool = False
    operator_confirmed_dedicated_notebook: bool = False
    actions_applied: int = 0
    actions_skipped: int = 0
    actions_failed: int = 0


def file_sha256(path: Path) -> str:
    return sha256_file(path)
