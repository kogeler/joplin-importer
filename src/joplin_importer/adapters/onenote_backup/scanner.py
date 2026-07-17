"""Read-only scanner for the newest local OneNote section backups."""

from __future__ import annotations

import html
import mimetypes
import re
import sys
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
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
    sha256_canonical_json,
    sha256_file,
    sha256_text,
)
from ...models.timeutil import now_utc_iso
from ...normalization.model import collect_links
from .discovery import BackupInventory, BackupSection, discover_latest_sections

Normalizer = Callable[[ContentFormat, str, dict[str, str]], Any]
DocumentLoader = Callable[[Path], Any]
_VERSION_PROXY_CONTEXT_PROPERTY = 0x3400347B
_ASPOSE_WRONG_NESTED_PROPERTY_TYPE = 0x11
_OPAQUE_FOUR_BYTE_COUNT_TYPE = 0x5
_HYPERLINK_FIELD_RE = re.compile(r'^\ufddfHYPERLINK\s+"([^"]+)"$')


def scan_onenote_backup(
    root: Path,
    writer: SnapshotWriter,
    *,
    tool_version: str,
    snapshot_id: str,
    root_mode: str = "manual",
    include_recycle_bin: bool = False,
    notebook_filter: str | None = None,
    normalizer: Normalizer | None = None,
    on_progress: Callable[[str], None] | None = None,
    inventory: BackupInventory | None = None,
    document_loader: DocumentLoader | None = None,
) -> Manifest:
    """Extract a latest-only snapshot without changing backup files."""

    started = now_utc_iso()
    progress = on_progress or (lambda _message: None)
    inventory = inventory or discover_latest_sections(
        root,
        include_recycle_bin=include_recycle_bin,
        notebook_filter=notebook_filter,
    )
    loader = document_loader or _load_aspose_document

    page_count = 0
    resource_count = 0
    section_error_count = 0
    artifact_entries: list[dict[str, str]] = []
    notebooks: set[str] = set()

    for section_index, section in enumerate(inventory.sections, start=1):
        notebooks.add(section.notebook_title)
        section_id = _stable_id(
            "section",
            section.notebook_title,
            *section.section_group_path,
            section.section_title,
        )
        try:
            file_digest = sha256_file(section.path)
            artifact_entries.append({"path": section.relative_path, "sha256": file_digest})
            document = loader(section.path)
            for page_order, page in enumerate(document):
                title = _page_title(page)
                page_id = _stable_id(
                    "page",
                    section.notebook_title,
                    *section.section_group_path,
                    section.section_title,
                    str(page_order),
                    title,
                )
                if writer.has_record(page_id):
                    continue
                record = _capture_page(
                    writer,
                    page,
                    page_id=page_id,
                    page_order=page_order,
                    title=title,
                    section=section,
                    section_id=section_id,
                    source_file_digest=file_digest,
                    normalizer=normalizer,
                )
                writer.write_record(record)
                page_count += 1
                resource_count += sum(
                    1 for resource in record.resources if resource.status == ResourceStatus.OK
                )
                if page_count % 50 == 0:
                    progress(f"scanned {page_count} pages from latest OneNote backups")
        except Exception as exc:
            section_error_count += 1
            writer.add_error(
                ErrorRecord(
                    scope="section",
                    item_id=section_id,
                    item_title=section.section_title,
                    message=(
                        f"failed to read latest backup {section.relative_path}: "
                        f"{_safe_error_message(exc, root)}"
                    ),
                    exception_type=type(exc).__name__,
                )
            )
        progress(
            f"processed {section_index}/{inventory.logical_section_count} latest backup sections"
        )

    coverage_notes: list[str] = []
    if section_error_count:
        coverage_notes.append(
            f"{section_error_count} latest backup section(s) failed; see errors.jsonl"
        )
    if inventory.older_versions_skipped:
        coverage_notes.append(
            f"{inventory.older_versions_skipped} older backup version(s) intentionally skipped"
        )
    if inventory.recycle_bin_files_skipped:
        coverage_notes.append(
            f"{inventory.recycle_bin_files_skipped} recycle-bin backup file(s) excluded"
        )

    adapter_version = _aspose_version()
    finished = now_utc_iso()
    manifest = Manifest(
        snapshot_id=snapshot_id,
        tool_version=tool_version,
        source_backend=SourceBackend.ONENOTE_BACKUP,
        audit_role=AuditRole.CORROBORATING,
        adapter_versions={"aspose-note": adapter_version},
        source_artifact_sha256=sha256_canonical_json(artifact_entries),
        scan_started_at_utc=started,
        scan_finished_at_utc=finished,
        configuration={
            "backup_root_mode": root_mode,
            "backup_selection": "latest",
            "parser_compatibility_patch": "version-proxy-context-count",
            "include_recycle_bin": str(include_recycle_bin).lower(),
            "notebook_filter": notebook_filter or "",
        },
        record_counts={
            "pages": page_count,
            "notebooks": len(notebooks),
            "sections": inventory.logical_section_count,
            "readable_sections": inventory.logical_section_count - section_error_count,
            "physical_backup_files": inventory.physical_file_count,
            "older_versions_skipped": inventory.older_versions_skipped,
            "resources": resource_count,
        },
        coverage_status="partial" if section_error_count else "complete",
        coverage_notes=coverage_notes,
    )
    metadata = ScanMetadata(
        started_at_utc=started,
        finished_at_utc=finished,
        host_os=sys.platform,
        tool_version=tool_version,
        adapter="onenote-backup",
        adapter_versions={"aspose-note": adapter_version},
        limitations=[
            "Local backups are point-in-time copies and can be older than current OneNote content",
            "Only the newest physical backup file for each logical section is read",
            "Backup pages have deterministic synthetic IDs because section files do not expose "
            "the live COM page ID through this adapter",
            "The pinned parser's VersionProxy ContextID-array override is corrected in memory; "
            "the source backup bytes are never changed",
        ],
        error_count=section_error_count,
    )
    writer.finalize(manifest, metadata)
    return manifest


def _capture_page(
    writer: SnapshotWriter,
    page: Any,
    *,
    page_id: str,
    page_order: int,
    title: str,
    section: BackupSection,
    section_id: str,
    source_file_digest: str,
    normalizer: Normalizer | None,
) -> PageRecord:
    render = _RenderContext(writer)
    body = _render_children(_node_children(page), render)
    html_text = f"<html><body>{body}</body></html>"
    raw_rel, raw_digest = writer.write_raw_content(
        page_id, html_text.encode("utf-8"), ".html"
    )
    record = PageRecord(
        source_backend=SourceBackend.ONENOTE_BACKUP,
        audit_role=AuditRole.CORROBORATING,
        source_page_id=page_id,
        notebook_id=_stable_id("notebook", section.notebook_title),
        notebook_title=section.notebook_title,
        section_group_path=list(section.section_group_path),
        section_id=section_id,
        section_title=section.section_title,
        page_title=title,
        page_level=int(getattr(page, "Level", 0) or 0),
        page_order=page_order,
        created_at=_datetime_iso(getattr(page, "CreationTime", None)),
        updated_at=_datetime_iso(getattr(page, "LastModifiedTime", None)),
        raw_content_path=raw_rel,
        raw_content_format=ContentFormat.HTML,
        raw_content_sha256=raw_digest,
        resources=render.resources,
        warnings=[
            f"source backup section: {section.relative_path}",
            f"source backup section sha256: {source_file_digest}",
            *render.warnings,
        ],
    )
    record.resource_hashes = sorted(
        resource.sha256 for resource in record.resources if resource.sha256
    )
    record.image_count = sum(1 for resource in record.resources if resource.is_image)
    record.attachment_count = sum(1 for resource in record.resources if not resource.is_image)

    if normalizer is not None:
        resource_map = {
            resource.source_reference: resource.sha256
            for resource in record.resources
            if resource.sha256
        }
        normalized = normalizer(ContentFormat.HTML, html_text, resource_map)
        model_rel, model_digest = writer.write_semantic_model(
            page_id, normalized.semantic_model
        )
        record.semantic_model_path = model_rel
        record.semantic_model_sha256 = model_digest
        record.normalizer_version = normalized.version
        record.normalized_text = normalized.normalized_text
        record.normalized_text_sha256 = normalized.normalized_text_sha256
        record.visible_text_length = len(normalized.normalized_text)
        record.link_targets = collect_links(normalized.semantic_model)
        record.warnings.extend(normalized.warnings)
    record.warnings = list(dict.fromkeys(record.warnings))
    return record


@dataclass(slots=True)
class _RenderContext:
    writer: SnapshotWriter
    resources: list[ResourceRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    image_index: int = 0
    attachment_index: int = 0


def _render_children(nodes: Iterable[Any], context: _RenderContext) -> str:
    items = list(nodes)
    parts: list[str] = []
    index = 0
    while index < len(items):
        node = items[index]
        if type(node).__name__ == "OutlineElement" and getattr(node, "NumberList", None):
            ordered = _is_ordered_list(getattr(node, "NumberList", None))
            list_items: list[str] = []
            while index < len(items):
                candidate = items[index]
                if type(candidate).__name__ != "OutlineElement":
                    break
                number_list = getattr(candidate, "NumberList", None)
                if number_list is None or _is_ordered_list(number_list) != ordered:
                    break
                list_items.append(
                    f"<li>{_render_children(_node_children(candidate), context)}</li>"
                )
                index += 1
            tag = "ol" if ordered else "ul"
            parts.append(f"<{tag}>{''.join(list_items)}</{tag}>")
            continue
        parts.append(_render_node(node, context))
        index += 1
    return "".join(parts)


def _render_node(node: Any, context: _RenderContext) -> str:
    kind = type(node).__name__
    if kind == "Title":
        return ""
    if kind == "RichText":
        return _render_rich_text(node)
    if kind == "Image":
        return _render_image(node, context)
    if kind == "AttachedFile":
        return _render_attachment(node, context)
    if kind == "Table":
        rows = []
        for row in _node_children(node):
            cells = [
                f"<td>{_render_children(_node_children(cell), context)}</td>"
                for cell in _node_children(row)
            ]
            rows.append(f"<tr>{''.join(cells)}</tr>")
        return f"<table>{''.join(rows)}</table>"
    if kind in {"Outline", "OutlineElement", "TableRow", "TableCell", "Page"}:
        return _render_children(_node_children(node), context)

    children = _node_children(node)
    context.warnings.append(f"unsupported OneNote backup object: {kind}")
    return _render_children(children, context) if children else ""


def _render_rich_text(node: Any) -> str:
    runs = list(getattr(node, "TextRuns", None) or [])
    if not runs:
        text = str(getattr(node, "Text", "") or "")
        content = _escape_text(text)
    else:
        rendered: list[str] = []
        index = 0
        while index < len(runs):
            run = runs[index]
            raw_text = str(getattr(run, "Text", "") or "")
            field = _HYPERLINK_FIELD_RE.fullmatch(raw_text)
            if field is not None and index + 1 < len(runs):
                # Aspose exposes legacy OneNote/Office links as two adjacent
                # runs: a hidden U+FDDF HYPERLINK instruction followed by the
                # visible field result. Restore the link without exporting the
                # internal instruction as visible text.
                visible = _escape_text(str(getattr(runs[index + 1], "Text", "") or ""))
                address = field.group(1).strip()
                if visible and address:
                    rendered.append(
                        f'<a href="{html.escape(address, quote=True)}">{visible}</a>'
                    )
                    index += 2
                    continue

            text = _escape_text(raw_text)
            style = getattr(run, "Style", None)
            address = str(getattr(style, "HyperlinkAddress", "") or "").strip()
            if address:
                text = f'<a href="{html.escape(address, quote=True)}">{text}</a>'
            rendered.append(text)
            index += 1
        content = "".join(rendered)
    return f"<p>{content}</p>" if content else ""


def _render_image(node: Any, context: _RenderContext) -> str:
    context.image_index += 1
    reference = f"backup-image:{context.image_index}"
    filename = _safe_filename(getattr(node, "FileName", None))
    data = bytes(getattr(node, "Bytes", b"") or b"")
    resource = _resource_record(
        context,
        data=data,
        reference=reference,
        filename=filename,
        media_type=_media_type(filename, getattr(node, "Format", None)),
        is_image=True,
    )
    alt = str(
        getattr(node, "AlternativeTextTitle", None)
        or getattr(node, "AlternativeTextDescription", None)
        or ""
    )
    if resource.status != ResourceStatus.OK:
        context.warnings.append("OneNote backup image has no readable binary data")
    return (
        f'<img src="{html.escape(reference, quote=True)}" '
        f'alt="{html.escape(alt, quote=True)}">'
    )


def _render_attachment(node: Any, context: _RenderContext) -> str:
    context.attachment_index += 1
    reference = f":/backup-file-{context.attachment_index}"
    filename = _safe_filename(getattr(node, "FileName", None)) or "attachment"
    data = bytes(getattr(node, "Bytes", b"") or b"")
    resource = _resource_record(
        context,
        data=data,
        reference=reference,
        filename=filename,
        media_type=_media_type(filename, None),
        is_image=False,
    )
    if resource.status != ResourceStatus.OK:
        context.warnings.append("OneNote backup attachment has no readable binary data")
    return (
        f'<p><a href="{html.escape(reference, quote=True)}">'
        f"{html.escape(filename)}</a></p>"
    )


def _resource_record(
    context: _RenderContext,
    *,
    data: bytes,
    reference: str,
    filename: str | None,
    media_type: str | None,
    is_image: bool,
) -> ResourceRecord:
    resource = ResourceRecord(
        source_reference=reference,
        original_filename=filename,
        media_type=media_type,
        is_image=is_image,
    )
    if data:
        stored_path, digest = context.writer.write_resource(data, _safe_extension(filename))
        resource.stored_path = stored_path
        resource.sha256 = digest
        resource.byte_length = len(data)
        resource.status = ResourceStatus.OK
    else:
        resource.status = ResourceStatus.MISSING
    context.resources.append(resource)
    return resource


def _page_title(page: Any) -> str:
    title = getattr(page, "Title", None)
    title_text = getattr(title, "TitleText", None)
    return str(getattr(title_text, "Text", "") or "").strip()


def _node_children(node: Any) -> list[Any]:
    if node is None or type(node).__name__ == "RichText":
        return []
    try:
        return list(iter(node))
    except TypeError:
        return []


def _is_ordered_list(number_list: Any) -> bool:
    number_format = str(getattr(number_list, "NumberFormat", "") or "")
    display_format = str(getattr(number_list, "Format", "") or "")
    return bool(number_format or "{0}" in display_format)


def _escape_text(value: str) -> str:
    return html.escape(value).replace("\r\n", "<br>").replace("\n", "<br>")


def _safe_filename(value: Any) -> str | None:
    if not value:
        return None
    return str(value).replace("\\", "/").rsplit("/", 1)[-1]


def _safe_extension(filename: str | None) -> str:
    if not filename:
        return ""
    suffix = Path(filename).suffix.casefold()
    if 1 < len(suffix) <= 11 and suffix[1:].isalnum():
        return suffix
    return ""


def _media_type(filename: str | None, image_format: Any) -> str | None:
    guessed = mimetypes.guess_type(filename or "")[0]
    if guessed:
        return guessed
    fmt = str(image_format or "").strip().lower().lstrip(".")
    return f"image/{fmt}" if fmt and fmt.isalnum() else None


def _stable_id(kind: str, *parts: str) -> str:
    return f"backup-{kind}-{sha256_text(chr(0).join(parts))[:32]}"


def _datetime_iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_error_message(exc: Exception, root: Path) -> str:
    message = str(exc)
    for root_text in {str(root), root.as_posix()}:
        message = message.replace(root_text, "<backup-root>")
    return message


def _load_aspose_document(path: Path) -> Any:
    import aspose.note._internal.onestore.parser as parser
    from aspose.note import Document

    with _version_proxy_parser_patch(parser):
        return Document(str(path))


@contextmanager
def _version_proxy_parser_patch(parser: Any):
    """Correct one bad Aspose type override without touching the `.one` file.

    ``0x3400347B`` is an ArrayOfContextIDs property. Its rgData contribution is
    a four-byte count, but Aspose 26.3.2 overrides it as a nested PropertySet,
    misaligning every property that follows. The public DOM never consumes
    this VersionProxy property, so reading its count as an opaque four-byte
    scalar preserves the stream cursor and all page content.
    """

    overrides = parser.PROPERTY_TYPE_OVERRIDES_RAW
    original = overrides.get(_VERSION_PROXY_CONTEXT_PROPERTY)
    should_patch = original == _ASPOSE_WRONG_NESTED_PROPERTY_TYPE
    if should_patch:
        overrides[_VERSION_PROXY_CONTEXT_PROPERTY] = _OPAQUE_FOUR_BYTE_COUNT_TYPE
    try:
        yield should_patch
    finally:
        if should_patch:
            overrides[_VERSION_PROXY_CONTEXT_PROPERTY] = original


def _aspose_version() -> str:
    try:
        return version("aspose-note")
    except PackageNotFoundError:  # pragma: no cover - dependency is required in production
        return "unknown"
