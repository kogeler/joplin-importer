"""Read-only OneNote COM inventory scan.

Enumerates the hierarchy, downloads page XML, extracts binary resources, and
writes a snapshot. One failing page is recorded and does not stop the scan.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

from ...models import (
    AuditRole,
    ContentFormat,
    ErrorRecord,
    Manifest,
    PageRecord,
    ResourceRecord,
    ResourceStatus,
    ScanMetadata,
    SnapshotWriter,
    SourceBackend,
)
from ...models.timeutil import now_utc_iso, utc_iso_from_string
from .api import OneNoteApi, OneNoteApiError, OneNoteProcessUnavailableError
from .hierarchy import PageStub, parse_hierarchy
from .page_parser import ParsedPage, parse_page_xml
from .quarantine import OneNoteQuarantine

Normalizer = Callable[[ContentFormat, str, dict[str, str]], Any]


def scan_onenote_com(
    api: OneNoteApi,
    writer: SnapshotWriter,
    *,
    tool_version: str,
    snapshot_id: str,
    include_binary: bool = True,
    include_recycle_bin: bool = False,
    notebook_filter: str | None = None,
    quarantine: OneNoteQuarantine | None = None,
    normalizer: Normalizer | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Manifest:
    """Run a full read-only COM inventory and finalize the snapshot."""
    started = now_utc_iso()
    progress = on_progress or (lambda _msg: None)

    hierarchy = parse_hierarchy(api.get_hierarchy())
    stubs = hierarchy.pages
    if notebook_filter is not None:
        stubs = [s for s in stubs if s.notebook_title == notebook_filter]
    skipped_recycled = 0
    if not include_recycle_bin:
        before = len(stubs)
        stubs = [s for s in stubs if not s.in_recycle_bin]
        skipped_recycled = before - len(stubs)

    quarantine_by_id = quarantine.by_page_id() if quarantine is not None else {}
    hierarchy_ids = {stub.page_id for stub in hierarchy.pages}
    selected_ids = {stub.page_id for stub in stubs}
    stale_quarantine_entries = [
        entry for page_id, entry in quarantine_by_id.items() if page_id not in hierarchy_ids
    ]
    out_of_scope_quarantine_count = len(
        set(quarantine_by_id).intersection(hierarchy_ids) - selected_ids
    )

    page_count = 0
    resource_count = 0
    error_count = 0
    quarantined_count = 0
    quarantine_title_mismatch_count = 0
    unattempted_after_process_failure = 0

    for entry in stale_quarantine_entries:
        writer.add_error(
            ErrorRecord(
                scope="page",
                item_id=entry.page_id,
                item_title=entry.expected_title,
                message=(
                    "quarantine entry does not match any page ID in the current "
                    f"OneNote hierarchy; reason: {entry.reason}"
                ),
                exception_type="StaleQuarantineEntry",
            )
        )

    for position, stub in enumerate(stubs):
        try:
            if writer.has_record(stub.page_id):
                continue  # resume support
            quarantine_entry = quarantine_by_id.get(stub.page_id)
            if quarantine_entry is not None:
                title_mismatch = quarantine_entry.expected_title != stub.title
                if title_mismatch:
                    quarantine_title_mismatch_count += 1
                detail = f"; reason: {quarantine_entry.reason}"
                if title_mismatch:
                    detail += (
                        f"; expected title {quarantine_entry.expected_title!r}, "
                        f"current title {stub.title!r}"
                    )
                writer.add_error(
                    ErrorRecord(
                        scope="page",
                        item_id=stub.page_id,
                        item_title=stub.title,
                        message="intentionally quarantined before GetPageContent" + detail,
                        exception_type="IntentionallyQuarantined",
                    )
                )
                quarantined_count += 1
                continue
            xml_text = api.get_page_content(stub.page_id, include_binary=include_binary)
            record = _build_page_record(stub, xml_text, writer)
            parsed = parse_page_xml(xml_text)
            resource_count += _capture_resources(writer, record, parsed)
            record.warnings.extend(hierarchy.warnings if page_count == 0 else [])
            record.warnings.extend(parsed.warnings)
            if stub.in_recycle_bin:
                record.warnings.append("page is in the OneNote recycle bin")
            if normalizer is not None:
                _apply_normalizer(writer, record, normalizer, xml_text)
            writer.write_record(record)
            page_count += 1
            if page_count % 50 == 0:
                progress(f"scanned {page_count}/{len(stubs)} pages")
        except OneNoteProcessUnavailableError as exc:
            error_count += 1
            unattempted_after_process_failure = len(stubs) - position - 1
            writer.add_error(
                ErrorRecord(
                    scope="page",
                    item_id=stub.page_id,
                    item_title=stub.title,
                    message=str(exc),
                    exception_type=type(exc).__name__,
                )
            )
            break
        except (OneNoteApiError, OSError, ValueError) as exc:
            error_count += 1
            writer.add_error(
                ErrorRecord(
                    scope="page",
                    item_id=stub.page_id,
                    item_title=stub.title,
                    message=str(exc),
                    exception_type=type(exc).__name__,
                )
            )

    coverage_notes = []
    if error_count:
        coverage_notes.append(f"{error_count} page(s) failed; see errors.jsonl")
    if unattempted_after_process_failure:
        coverage_notes.append(
            "OneNote COM process became unavailable; stopped without attempting "
            f"{unattempted_after_process_failure} remaining hierarchy page(s)"
        )
    if quarantined_count:
        coverage_notes.append(
            f"{quarantined_count} page(s) intentionally quarantined before "
            "GetPageContent; see errors.jsonl"
        )
    if stale_quarantine_entries:
        coverage_notes.append(
            f"{len(stale_quarantine_entries)} quarantine entry/entries do not match "
            "the current hierarchy; see errors.jsonl"
        )
    if quarantine_title_mismatch_count:
        coverage_notes.append(
            f"{quarantine_title_mismatch_count} quarantined page(s) had a different "
            "current title and were skipped by exact page ID"
        )
    if out_of_scope_quarantine_count:
        coverage_notes.append(
            f"{out_of_scope_quarantine_count} quarantine entry/entries were outside "
            "the selected notebook/recycle-bin scope"
        )
    if skipped_recycled:
        coverage_notes.append(f"{skipped_recycled} recycle-bin page(s) excluded")

    quarantine_issue_count = quarantined_count + len(stale_quarantine_entries)
    total_issue_count = error_count + quarantine_issue_count

    manifest = Manifest(
        snapshot_id=snapshot_id,
        tool_version=tool_version,
        source_backend=SourceBackend.ONENOTE_COM,
        audit_role=AuditRole.AUTHORITATIVE_CURRENT,
        scan_started_at_utc=started,
        scan_finished_at_utc=now_utc_iso(),
        configuration={
            "include_binary": str(include_binary).lower(),
            "include_recycle_bin": str(include_recycle_bin).lower(),
            "notebook_filter": notebook_filter or "",
            "xml_namespace": hierarchy.namespace,
            "quarantine_enabled": str(quarantine is not None).lower(),
            "quarantine_digest": quarantine.digest() if quarantine is not None else "",
            "quarantine_entries": str(len(quarantine_by_id)),
        },
        record_counts={
            "pages": page_count,
            "notebooks": hierarchy.notebook_count,
            "sections": hierarchy.section_count,
            "resources": resource_count,
            "quarantined_pages": quarantined_count,
            "stale_quarantine_entries": len(stale_quarantine_entries),
            "quarantine_title_mismatches": quarantine_title_mismatch_count,
            "out_of_scope_quarantine_entries": out_of_scope_quarantine_count,
            "unattempted_after_process_failure": unattempted_after_process_failure,
        },
        coverage_status="partial" if total_issue_count else "complete",
        coverage_notes=coverage_notes,
    )
    metadata = ScanMetadata(
        started_at_utc=started,
        finished_at_utc=now_utc_iso(),
        host_os=sys.platform,
        tool_version=tool_version,
        adapter="onenote-com",
        limitations=[
            "COM snapshot reflects current live OneNote content, not the historical "
            "migration input; differences newer than the import may be source drift"
        ],
        error_count=total_issue_count,
    )
    writer.finalize(manifest, metadata)
    return manifest


def _build_page_record(stub: PageStub, xml_text: str, writer: SnapshotWriter) -> PageRecord:
    rel, digest = writer.write_raw_content(stub.page_id, xml_text.encode("utf-8"), ".xml")
    return PageRecord(
        source_backend=SourceBackend.ONENOTE_COM,
        audit_role=AuditRole.AUTHORITATIVE_CURRENT,
        source_page_id=stub.page_id,
        notebook_id=stub.notebook_id,
        notebook_title=stub.notebook_title,
        section_group_path=stub.section_group_path,
        section_id=stub.section_id,
        section_title=stub.section_title,
        page_title=stub.title,
        page_level=stub.level,
        page_order=stub.order,
        created_at=utc_iso_from_string(stub.created_at),
        updated_at=utc_iso_from_string(stub.updated_at),
        raw_content_path=rel,
        raw_content_format=ContentFormat.ONENOTE_XML,
        raw_content_sha256=digest,
    )


def _capture_resources(writer: SnapshotWriter, record: PageRecord, parsed: ParsedPage) -> int:
    captured = 0
    for res in parsed.resources:
        resource = ResourceRecord(
            source_reference=res.source_reference,
            original_filename=res.filename,
            media_type=res.media_type,
            is_image=res.kind == "image",
            warnings=list(res.warnings),
        )
        if res.data is not None:
            rel, digest = writer.write_resource(res.data)
            resource.stored_path = rel
            resource.sha256 = digest
            resource.byte_length = len(res.data)
            resource.status = ResourceStatus.OK
            captured += 1
        else:
            resource.status = ResourceStatus.MISSING
        record.resources.append(resource)

    record.resource_hashes = sorted(r.sha256 for r in record.resources if r.sha256)
    record.image_count = sum(1 for r in record.resources if r.is_image)
    record.attachment_count = sum(1 for r in record.resources if not r.is_image)
    return captured


def _apply_normalizer(
    writer: SnapshotWriter, record: PageRecord, normalizer: Normalizer, xml_text: str
) -> None:
    resource_map = {r.source_reference: r.sha256 for r in record.resources if r.sha256}
    normalized = normalizer(ContentFormat.ONENOTE_XML, xml_text, resource_map)
    rel, digest = writer.write_semantic_model(record.source_page_id, normalized.semantic_model)
    record.semantic_model_path = rel
    record.semantic_model_sha256 = digest
    record.normalizer_version = normalized.version
    record.normalized_text = normalized.normalized_text
    record.normalized_text_sha256 = normalized.normalized_text_sha256
    record.visible_text_length = len(normalized.normalized_text)
    record.link_targets = _collect_links(normalized.semantic_model)
    record.warnings.extend(normalized.warnings)


def _collect_links(semantic_model: dict) -> list[str]:
    from ...normalization.model import collect_links

    return collect_links(semantic_model)
