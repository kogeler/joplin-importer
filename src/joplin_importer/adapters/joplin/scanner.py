"""Read-only Joplin inventory scan.

Walks folders, notes (normal + trashed + conflict), and resources through the
Data API and writes a snapshot. Failures on one note are recorded and do not
stop the scan.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from collections.abc import Callable
from typing import Any

from ...models import (
    AuditRole,
    ContentFormat,
    ErrorRecord,
    Manifest,
    NoteRecord,
    NoteStatus,
    ResourceRecord,
    ResourceStatus,
    ScanMetadata,
    SnapshotWriter,
    SourceBackend,
)
from ...models.timeutil import now_utc_iso, utc_iso_from_epoch_ms
from ...transport import TransportError
from .client import JoplinApiError, JoplinClient

RESOURCE_REF_RE = re.compile(r":/([0-9a-fA-F]{32})")
ONENOTE_SOURCE_URL_RE = re.compile(r"^onenote://page/(.+)$")
IMPORTER_MARKER_RE = re.compile(
    r"(?:joplin-importer|ojr):onenote_page_id=([^\s<>\"']+)"
)

# Pluggable normalizer: (format, raw_text, resource_map) -> NormalizedContent-like
Normalizer = Callable[[ContentFormat, str, dict[str, str]], Any]


def detect_content_format(markup_language: int | None, body: str) -> ContentFormat:
    """Derive the stored format; never assume it from the migration source."""
    has_html_tags = bool(re.search(r"<[a-zA-Z][^>]*>", body))
    if markup_language == 2:
        return ContentFormat.HTML
    if markup_language == 1:
        return ContentFormat.MIXED if has_html_tags else ContentFormat.MARKDOWN
    if markup_language is None:
        return ContentFormat.UNKNOWN
    return ContentFormat.UNKNOWN


def extract_embedded_onenote_id(source_url: str, body: str) -> str | None:
    """Recover a OneNote page ID left by a previous import or recovery run."""
    match = ONENOTE_SOURCE_URL_RE.match(source_url or "")
    if match:
        return urllib.parse.unquote(match.group(1))
    match = IMPORTER_MARKER_RE.search(body or "")
    if match:
        return urllib.parse.unquote(match.group(1))
    return None


def extract_resource_refs(body: str) -> list[str]:
    """All ``:/<32-hex>`` resource IDs referenced by a note body, in order."""
    seen: list[str] = []
    for ref in RESOURCE_REF_RE.findall(body or ""):
        low = ref.lower()
        if low not in seen:
            seen.append(low)
    return seen


def build_folder_paths(folders: list[dict[str, Any]]) -> dict[str, list[str]]:
    """folder id -> full path (list of titles from root to folder)."""
    by_id = {f["id"]: f for f in folders}
    paths: dict[str, list[str]] = {}

    def resolve(folder_id: str) -> list[str]:
        if folder_id in paths:
            return paths[folder_id]
        chain: list[str] = []
        current: str | None = folder_id
        hops = 0
        while current and current in by_id and hops < 100:  # cycle guard
            folder = by_id[current]
            chain.append(folder.get("title", ""))
            current = folder.get("parent_id") or None
            hops += 1
        chain.reverse()
        paths[folder_id] = chain
        return chain

    for folder in folders:
        resolve(folder["id"])
    return paths


def scan_joplin(
    client: JoplinClient,
    writer: SnapshotWriter,
    *,
    tool_version: str,
    snapshot_id: str,
    download_resources: bool = True,
    normalizer: Normalizer | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Manifest:
    """Run a full read-only inventory and finalize the snapshot."""
    started = now_utc_iso()
    progress = on_progress or (lambda _msg: None)

    capabilities = client.probe_capabilities()
    folders = list(client.iter_folders())
    folder_paths = build_folder_paths(folders)
    trashed_folders = {f["id"] for f in folders if f.get("deleted_time")}
    active_folder_count = len(folders) - len(trashed_folders)

    note_count = 0
    normal_note_count = 0
    trashed_note_count = 0
    conflict_note_count = 0
    resource_count = 0
    error_count = 0

    for note in client.iter_notes():
        note_id = note.get("id", "")
        try:
            if writer.has_record(note_id):
                continue  # resume support: already captured in staging
            record = _build_note_record(note, folder_paths, trashed_folders)
            if record.status == NoteStatus.NORMAL:
                normal_note_count += 1
            elif record.status == NoteStatus.TRASHED:
                trashed_note_count += 1
            else:
                conflict_note_count += 1
            body = note.get("body") or ""
            rel, digest = writer.write_raw_content(note_id, body.encode("utf-8"), ".md")
            record.raw_content_path = rel
            record.raw_content_sha256 = digest

            resource_count += _capture_resources(
                client, writer, record, body, download_resources
            )

            if normalizer is not None:
                _apply_normalizer(writer, record, normalizer, body)

            writer.write_record(record)
            note_count += 1
            if note_count % 100 == 0:
                progress(f"scanned {note_count} notes")
        except (JoplinApiError, TransportError, OSError, ValueError) as exc:
            error_count += 1
            writer.add_error(
                ErrorRecord(
                    scope="page",
                    item_id=note_id,
                    item_title=str(note.get("title", "")),
                    message=str(exc),
                    exception_type=type(exc).__name__,
                )
            )

    manifest = Manifest(
        snapshot_id=snapshot_id,
        tool_version=tool_version,
        source_backend=SourceBackend.JOPLIN_API,
        audit_role=AuditRole.TARGET,
        scan_started_at_utc=started,
        scan_finished_at_utc=now_utc_iso(),
        configuration={
            "download_resources": str(download_resources).lower(),
            **{f"capability.{k}": str(v).lower() for k, v in capabilities.items()},
        },
        record_counts={
            "notes": note_count,
            "normal_notes": normal_note_count,
            "trashed_notes": trashed_note_count,
            "conflict_notes": conflict_note_count,
            "folders": len(folders),
            "active_folders": active_folder_count,
            "trashed_folders": len(trashed_folders),
            "resources": resource_count,
        },
        coverage_status="partial" if error_count else "complete",
        coverage_notes=[f"{error_count} item(s) failed; see errors.jsonl"] if error_count else [],
    )
    metadata = ScanMetadata(
        started_at_utc=started,
        finished_at_utc=now_utc_iso(),
        host_os=sys.platform,
        tool_version=tool_version,
        adapter="joplin-api",
        api_versions={"ping": "JoplinClipperServer"},
        error_count=error_count,
    )
    writer.finalize(manifest, metadata)
    return manifest


def _build_note_record(
    note: dict[str, Any],
    folder_paths: dict[str, list[str]],
    trashed_folders: set[str],
) -> NoteRecord:
    note_id = note["id"]
    parent_id = note.get("parent_id") or ""
    body = note.get("body") or ""
    markup_language = note.get("markup_language")

    if note.get("deleted_time") or parent_id in trashed_folders:
        status = NoteStatus.TRASHED
    elif note.get("is_conflict"):
        status = NoteStatus.CONFLICT
    else:
        status = NoteStatus.NORMAL

    return NoteRecord(
        joplin_note_id=note_id,
        parent_notebook_id=parent_id,
        notebook_path=folder_paths.get(parent_id, []),
        title=note.get("title") or "",
        markup_language=markup_language,
        content_format=detect_content_format(markup_language, body),
        has_body_html=bool(note.get("body_html")),
        status=status,
        created_at=utc_iso_from_epoch_ms(note.get("created_time")),
        updated_at=utc_iso_from_epoch_ms(note.get("updated_time")),
        user_created_at=utc_iso_from_epoch_ms(note.get("user_created_time")),
        user_updated_at=utc_iso_from_epoch_ms(note.get("user_updated_time")),
        source_url=note.get("source_url") or "",
        source_application=note.get("source_application") or "",
        embedded_onenote_page_id=extract_embedded_onenote_id(
            note.get("source_url") or "", body
        ),
        raw_content_format=detect_content_format(markup_language, body),
    )


def _capture_resources(
    client: JoplinClient,
    writer: SnapshotWriter,
    record: NoteRecord,
    body: str,
    download: bool,
) -> int:
    referenced = extract_resource_refs(body)
    attached: dict[str, dict[str, Any]] = {}
    for res in client.iter_note_resources(record.joplin_note_id):
        attached[res["id"].lower()] = res

    captured = 0
    for res_id, res in attached.items():
        mime = res.get("mime") or ""
        resource = ResourceRecord(
            source_reference=f":/{res_id}",
            original_filename=res.get("filename") or res.get("title") or None,
            media_type=mime or None,
            byte_length=res.get("size"),
            is_image=mime.startswith("image/"),
        )
        if download:
            try:
                data = client.get_resource_file(res_id)
                rel, digest = writer.write_resource(data)
                resource.stored_path = rel
                resource.sha256 = digest
                resource.byte_length = len(data)
                resource.status = ResourceStatus.OK
            except (JoplinApiError, OSError) as exc:
                resource.status = ResourceStatus.UNREADABLE
                resource.warnings.append(str(exc))
        else:
            resource.status = ResourceStatus.SKIPPED
        record.resources.append(resource)
        captured += 1

    # Broken references stay visible as explicit records.
    for ref in referenced:
        if ref not in attached:
            record.resources.append(
                ResourceRecord(
                    source_reference=f":/{ref}",
                    status=ResourceStatus.MISSING,
                    warnings=["referenced in body but not attached to the note"],
                )
            )

    record.resource_hashes = sorted(r.sha256 for r in record.resources if r.sha256)
    record.image_count = sum(1 for r in record.resources if r.is_image)
    record.attachment_count = sum(
        1 for r in record.resources if not r.is_image and r.status != ResourceStatus.MISSING
    )
    return captured


def _apply_normalizer(
    writer: SnapshotWriter, record: NoteRecord, normalizer: Normalizer, body: str
) -> None:
    resource_map = {r.source_reference: r.sha256 for r in record.resources if r.sha256}
    normalized = normalizer(record.content_format, body, resource_map)
    rel, digest = writer.write_semantic_model(
        record.joplin_note_id, normalized.semantic_model
    )
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
