"""Repair plan generation.

The plan is deterministic for a given pair of snapshots (except the creation
timestamp, which is recorded once and embedded in generated bodies so hashes
stay reproducible). The plan file is immutable; approvals live in a separate
file bound to the plan digest.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..matching.results import AuditResult, Finding
from ..models import FindingKind, PageRecord, SnapshotReader
from ..models.enums import CauseClass, MatchConfidence, RepairActionType
from ..models.hashing import sha256_canonical_json, sha256_text
from ..models.timeutil import now_utc_iso
from ..normalization import Normalizer
from ..normalization.model import semantic_hash
from .content import build_recovery_html
from .models import (
    RECOVERY_NOTEBOOK,
    ApprovalFile,
    Preconditions,
    RepairAction,
    RepairPlan,
    file_sha256,
)

#: findings that produce a create-recovery-copy action in create-only mode
_RECOVERABLE_KINDS = {
    FindingKind.SOURCE_PAGE_MISSING,
    FindingKind.EMPTY_BODY,
    FindingKind.TRUNCATED_TEXT,
}


def build_repair_plan(
    audit: AuditResult,
    source: SnapshotReader,
    *,
    tool_version: str,
    target_instance_fingerprint: str,
    mode: str = "create-only",
    destination_notebook: str = RECOVERY_NOTEBOOK,
    include_drift: bool = False,
    created_at_utc: str | None = None,
) -> tuple[RepairPlan, dict[str, str]]:
    """Build the plan; returns (plan, {action_id: recovery_html_body}).

    Bodies are returned separately so callers can stage them next to the plan;
    the plan itself stores only hashes.
    """
    if mode != "create-only":
        raise ValueError("only create-only mode is supported in this version")

    created_at = created_at_utc or now_utc_iso()
    normalizer = Normalizer()
    pages = {p.source_page_id: p for p in source.iter_pages()}
    matches = {m.source_page_id: m for m in audit.matches if m.source_page_id}

    actions: list[RepairAction] = []
    bodies: dict[str, str] = {}
    seen_pages: set[str] = set()

    for finding in sorted(audit.findings, key=_finding_sort_key):
        if finding.kind not in _RECOVERABLE_KINDS:
            continue
        if finding.cause == CauseClass.SOURCE_DRIFT and not include_drift:
            continue
        page_id = finding.source_page_id
        if not page_id or page_id in seen_pages:
            continue
        page = pages.get(page_id)
        if page is None:
            continue
        seen_pages.add(page_id)

        semantic_model = _load_model(source, page, normalizer)
        action_id = sha256_text(
            f"{source.manifest.snapshot_id}|{page_id}|create-recovery-copy"
        )[:16]
        body_html = build_recovery_html(
            page,
            semantic_model,
            source_backend=str(page.source_backend),
            plan_created_at_utc=created_at,
            action_id=action_id,
            warnings=[_reason(finding)],
        )
        match = matches.get(page_id)
        action = RepairAction(
            action_id=action_id,
            idempotency_key=f"joplin-importer:{action_id}",
            source_page_id=page_id,
            existing_joplin_note_id=finding.joplin_note_id,
            action=RepairActionType.CREATE_RECOVERY_COPY,
            confidence=match.confidence if match else MatchConfidence.UNMATCHED,
            reason=_reason(finding),
            destination_notebook=destination_notebook,
            expected_title=page.page_title or "Untitled recovered page",
            intended_content_format="html",
            expected_body_sha256=sha256_text(body_html),
            expected_semantic_sha256=semantic_hash(semantic_model),
            expected_resource_hashes=sorted(set(page.resource_hashes)),
            preconditions=Preconditions(),
        )
        actions.append(action)
        bodies[action_id] = body_html

    plan = RepairPlan(
        plan_id=sha256_canonical_json(
            {
                "source": source.manifest.snapshot_id,
                "target": audit.summary.target_snapshot_id,
                "actions": [a.action_id for a in actions],
            }
        )[:16],
        tool_version=tool_version,
        created_at_utc=created_at,
        mode=mode,
        source_snapshot_id=audit.summary.source_snapshot_id,
        target_snapshot_id=audit.summary.target_snapshot_id,
        source_manifest_hash=audit.summary.source_manifest_hash,
        target_manifest_hash=audit.summary.target_manifest_hash,
        target_instance_fingerprint=target_instance_fingerprint,
        destination_notebook=destination_notebook,
        actions=actions,
    )
    return plan, bodies


def write_plan(plan: RepairPlan, bodies: dict[str, str], output: Path) -> Path:
    """Write repair-plan.json plus staged bodies under <output>.bodies/."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    bodies_dir = output.parent / (output.stem + ".bodies")
    bodies_dir.mkdir(exist_ok=True)
    for action_id, body in bodies.items():
        (bodies_dir / f"{action_id}.html").write_text(body, encoding="utf-8")
    return output


def load_plan(path: Path) -> tuple[RepairPlan, str]:
    """Load a plan file; returns (plan, sha256 of the exact file bytes)."""
    digest = file_sha256(path)
    plan = RepairPlan.model_validate_json(path.read_text(encoding="utf-8"))
    return plan, digest


def load_body(plan_path: Path, action_id: str) -> str:
    bodies_dir = plan_path.parent / (plan_path.stem + ".bodies")
    return (bodies_dir / f"{action_id}.html").read_text(encoding="utf-8")


def build_approval(
    plan_path: Path,
    *,
    action_ids: list[str] | None = None,
    operator: str = "",
    note: str = "",
) -> ApprovalFile:
    plan, digest = load_plan(plan_path)
    selected = action_ids if action_ids is not None else [a.action_id for a in plan.actions]
    known = {a.action_id for a in plan.actions}
    unknown = [a for a in selected if a not in known]
    if unknown:
        raise ValueError(f"unknown action ids: {unknown}")
    return ApprovalFile(
        repair_plan_sha256=digest,
        approved_action_ids=selected,
        operator=operator,
        note=note,
    )


def load_approval(path: Path) -> tuple[ApprovalFile, str]:
    digest = file_sha256(path)
    approval = ApprovalFile.model_validate_json(path.read_text(encoding="utf-8"))
    return approval, digest


def verify_approval(plan_path: Path, approval: ApprovalFile) -> None:
    """Fail hard when an approval refers to a different plan digest."""
    actual = file_sha256(plan_path)
    if approval.repair_plan_sha256 != actual:
        raise ValueError(
            "approval file was generated for a different repair plan: "
            f"approval digest {approval.repair_plan_sha256[:12]}…, "
            f"actual plan digest {actual[:12]}…"
        )


def _load_model(source: SnapshotReader, page: PageRecord, normalizer: Normalizer):
    if page.semantic_model_path and page.normalizer_version == normalizer.version:
        try:
            return json.loads(source.read_relative(page.semantic_model_path))
        except Exception:  # noqa: S110 - fall through to re-normalizing from raw
            pass
    if not page.raw_content_path:
        return {"kind": "doc", "version": "1", "children": []}
    raw = source.read_relative(page.raw_content_path).decode("utf-8")
    resource_map = {r.source_reference: r.sha256 for r in page.resources if r.sha256}
    from ..models.enums import ContentFormat

    return normalizer.normalize(
        ContentFormat(page.raw_content_format), raw, resource_map
    ).semantic_model


def _reason(finding: Finding) -> str:
    return f"{finding.kind}: {finding.explanation}"


def _finding_sort_key(finding: Finding) -> tuple:
    return (str(finding.kind), finding.source_page_id or "", finding.joplin_note_id or "")
