"""Deterministic post-export validation using source and Joplin snapshots."""

from __future__ import annotations

from collections import defaultdict

from ..models import NoteRecord, NoteStatus, SnapshotReader
from ..models.timeutil import now_utc_iso
from .models import ExportPlan, ExportValidationIssue, ExportValidationReport


def validate_export(
    source: SnapshotReader,
    target: SnapshotReader,
    plan: ExportPlan,
    *,
    strict_profile: bool = False,
) -> ExportValidationReport:
    issues: list[ExportValidationIssue] = []
    if source.manifest.snapshot_id != plan.source_snapshot_id:
        issues.append(
            ExportValidationIssue(
                kind="wrong-source-snapshot",
                expected=plan.source_snapshot_id,
                actual=source.manifest.snapshot_id,
            )
        )
    if source.manifest.coverage_status != "complete":
        issues.append(ExportValidationIssue(kind="source-coverage-not-complete"))
    if target.manifest.coverage_status != "complete":
        issues.append(ExportValidationIssue(kind="target-coverage-not-complete"))
    for problem in source.verify_checksums():
        issues.append(ExportValidationIssue(kind="source-checksum", actual=problem))
    for problem in target.verify_checksums():
        issues.append(ExportValidationIssue(kind="target-checksum", actual=problem))

    pages = {page.source_page_id: page for page in source.iter_pages()}
    expected_roots = {
        folder.title for folder in plan.folders if folder.parent_node_id is None
    }
    managed_target_notes = [
        note
        for note in target.iter_notes()
        if note.status == NoteStatus.NORMAL
        and note.notebook_path
        and note.notebook_path[0] in expected_roots
    ]
    notes_by_source: dict[str, list[NoteRecord]] = defaultdict(list)
    for note in managed_target_notes:
        if not note.embedded_onenote_page_id:
            issues.append(
                ExportValidationIssue(
                    kind="unexpected-note-without-source-id",
                    joplin_note_ids=[note.joplin_note_id],
                    actual=" / ".join(note.notebook_path + [note.title]),
                )
            )
            continue
        notes_by_source[note.embedded_onenote_page_id].append(note)

    validated = 0
    for planned_note in plan.notes:
        page = pages.get(planned_note.source_page_id)
        matches = notes_by_source.pop(planned_note.source_page_id, [])
        if page is None:
            issues.append(
                ExportValidationIssue(
                    kind="planned-source-page-missing",
                    source_page_id=planned_note.source_page_id,
                )
            )
            continue
        if not matches:
            issues.append(
                ExportValidationIssue(
                    kind="exported-note-missing",
                    source_page_id=planned_note.source_page_id,
                )
            )
            continue
        if len(matches) > 1:
            issues.append(
                ExportValidationIssue(
                    kind="duplicate-exported-note",
                    source_page_id=planned_note.source_page_id,
                    joplin_note_ids=[note.joplin_note_id for note in matches],
                )
            )
            continue
        note = matches[0]
        validated += 1
        note_ids = [note.joplin_note_id]
        expected_path = [page.notebook_title, *page.section_group_path, page.section_title]
        if note.notebook_path != expected_path:
            issues.append(
                ExportValidationIssue(
                    kind="wrong-export-path",
                    source_page_id=page.source_page_id,
                    joplin_note_ids=note_ids,
                    expected=" / ".join(expected_path),
                    actual=" / ".join(note.notebook_path),
                )
            )
        expected_title = page.page_title or "Untitled Page"
        if note.title != expected_title:
            issues.append(
                ExportValidationIssue(
                    kind="wrong-export-title",
                    source_page_id=page.source_page_id,
                    joplin_note_ids=note_ids,
                    expected=expected_title,
                    actual=note.title,
                )
            )
        if note.normalized_text != page.normalized_text:
            issues.append(
                ExportValidationIssue(
                    kind="normalized-text-mismatch",
                    source_page_id=page.source_page_id,
                    joplin_note_ids=note_ids,
                    expected=page.normalized_text_sha256 or "",
                    actual=note.normalized_text_sha256 or "",
                )
            )
        if set(note.resource_hashes) != set(page.resource_hashes):
            issues.append(
                ExportValidationIssue(
                    kind="resource-hash-mismatch",
                    source_page_id=page.source_page_id,
                    joplin_note_ids=note_ids,
                    expected=",".join(sorted(set(page.resource_hashes))),
                    actual=",".join(sorted(set(note.resource_hashes))),
                )
            )

    for source_page_id, notes in sorted(notes_by_source.items()):
        issues.append(
            ExportValidationIssue(
                kind="unexpected-exported-source-id",
                source_page_id=source_page_id,
                joplin_note_ids=[note.joplin_note_id for note in notes],
            )
        )

    # A replace-managed export leaves the previous importer tree in Joplin's trash
    # for recovery. Strict validation describes the active profile, not that
    # intentionally retained trash history.
    target_folders = target.manifest.record_counts.get(
        "active_folders", target.manifest.record_counts.get("folders", 0)
    )
    target_notes = target.manifest.record_counts.get(
        "normal_notes", target.manifest.record_counts.get("notes", 0)
    )
    if strict_profile and target_folders != len(plan.folders):
        issues.append(
            ExportValidationIssue(
                kind="strict-profile-folder-count",
                expected=str(len(plan.folders)),
                actual=str(target_folders),
            )
        )
    if strict_profile and target_notes != len(plan.notes):
        issues.append(
            ExportValidationIssue(
                kind="strict-profile-note-count",
                expected=str(len(plan.notes)),
                actual=str(target_notes),
            )
        )

    return ExportValidationReport(
        created_at_utc=now_utc_iso(),
        plan_id=plan.plan_id,
        source_snapshot_id=source.manifest.snapshot_id,
        target_snapshot_id=target.manifest.snapshot_id,
        strict_profile=strict_profile,
        result="failed" if issues else "ok",
        planned_folders=len(plan.folders),
        planned_notes=len(plan.notes),
        target_folders=target_folders,
        target_notes=target_notes,
        validated_notes=validated,
        issues=issues,
    )
