"""Read-only Microsoft Graph OneNote scan.

Traverses notebooks -> section groups -> sections -> pages, downloads page
HTML and referenced resources, and writes a corroborating snapshot. Graph
limitations are recorded in the scan metadata.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from typing import Any

from bs4 import BeautifulSoup

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
from ...transport import HttpTransport, TransportError, TransportMode
from .auth import acquire_token_device_code
from .client import GRAPH_BASE_URL, GraphApiError, GraphClient

Normalizer = Callable[[ContentFormat, str, dict[str, str]], Any]

_RESOURCE_URL_RE = re.compile(r"/onenote/resources/([^/]+)/")


def scan_onenote_graph(
    client_id: str,
    writer: SnapshotWriter,
    *,
    tool_version: str,
    snapshot_id: str,
    notebook_filter: str | None = None,
    download_resources: bool = True,
    normalizer: Normalizer | None = None,
    on_progress: Callable[[str], None] | None = None,
    client: GraphClient | None = None,
    account_label: str = "",
) -> Manifest:
    """Authenticate (unless a client is injected) and run a full scan."""
    if client is None:  # pragma: no cover - requires interactive auth
        token, account_label = acquire_token_device_code(client_id)
        transport = HttpTransport(
            GRAPH_BASE_URL,
            mode=TransportMode.READ_ONLY,
            token=token,
            token_in="header",  # noqa: S106 - transport option, not a secret
        )
        client = GraphClient(transport)
    return _scan(
        client,
        writer,
        tool_version=tool_version,
        snapshot_id=snapshot_id,
        notebook_filter=notebook_filter,
        download_resources=download_resources,
        normalizer=normalizer,
        on_progress=on_progress,
        account_label=account_label,
    )


def _scan(
    client: GraphClient,
    writer: SnapshotWriter,
    *,
    tool_version: str,
    snapshot_id: str,
    notebook_filter: str | None,
    download_resources: bool,
    normalizer: Normalizer | None,
    on_progress: Callable[[str], None] | None,
    account_label: str,
) -> Manifest:
    started = now_utc_iso()
    progress = on_progress or (lambda _msg: None)

    page_count = 0
    resource_count = 0
    error_count = 0
    notebook_count = 0
    section_count = 0

    for notebook in client.iter_notebooks():
        if notebook_filter and notebook.get("displayName") != notebook_filter:
            continue
        notebook_count += 1
        for section, group_path in _iter_sections(client, notebook):
            section_count += 1
            try:
                pages = list(client.iter_section_pages(section["id"]))
            except (GraphApiError, TransportError) as exc:
                error_count += 1
                writer.add_error(
                    ErrorRecord(
                        scope="section",
                        item_id=section.get("id", ""),
                        item_title=section.get("displayName", ""),
                        message=str(exc),
                        exception_type=type(exc).__name__,
                    )
                )
                continue
            for order, page in enumerate(pages):
                try:
                    if writer.has_record(page["id"]):
                        continue  # resume support
                    record = _capture_page(
                        client,
                        writer,
                        page,
                        notebook,
                        section,
                        group_path,
                        order,
                        download_resources,
                        normalizer,
                    )
                    writer.write_record(record)
                    page_count += 1
                    if page_count % 50 == 0:
                        progress(f"scanned {page_count} pages")
                    resource_count += sum(
                        1 for r in record.resources if r.status == ResourceStatus.OK
                    )
                except (GraphApiError, TransportError, OSError, ValueError) as exc:
                    error_count += 1
                    writer.add_error(
                        ErrorRecord(
                            scope="page",
                            item_id=page.get("id", ""),
                            item_title=page.get("title", ""),
                            message=str(exc),
                            exception_type=type(exc).__name__,
                        )
                    )

    manifest = Manifest(
        snapshot_id=snapshot_id,
        tool_version=tool_version,
        source_backend=SourceBackend.ONENOTE_GRAPH,
        audit_role=AuditRole.CORROBORATING,
        scan_started_at_utc=started,
        scan_finished_at_utc=now_utc_iso(),
        configuration={
            "notebook_filter": notebook_filter or "",
            "download_resources": str(download_resources).lower(),
        },
        record_counts={
            "pages": page_count,
            "notebooks": notebook_count,
            "sections": section_count,
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
        adapter="onenote-graph",
        account_scope=account_label,
        limitations=[
            "Graph is a corroborating source, not a full backup API; an omission "
            "here does not prove the content is absent from OneNote",
            "Graph page HTML is a conversion of OneNote content and may not "
            "include every object",
        ],
        error_count=error_count,
    )
    writer.finalize(manifest, metadata)
    return manifest


def _iter_sections(client: GraphClient, notebook: dict[str, Any]):
    """Yield (section, section_group_path) for the whole notebook tree."""
    notebook_id = notebook["id"]
    yield from (
        (section, [])
        for section in client.iter_sections(f"/me/onenote/notebooks/{notebook_id}/sections")
    )
    stack: list[tuple[dict[str, Any], list[str]]] = [
        (group, [group.get("displayName", "")])
        for group in client.iter_section_groups(
            f"/me/onenote/notebooks/{notebook_id}/sectionGroups"
        )
    ]
    while stack:
        group, path = stack.pop()
        group_id = group["id"]
        for section in client.iter_sections(f"/me/onenote/sectionGroups/{group_id}/sections"):
            yield section, path
        for child in client.iter_section_groups(
            f"/me/onenote/sectionGroups/{group_id}/sectionGroups"
        ):
            stack.append((child, [*path, child.get("displayName", "")]))


def _capture_page(
    client: GraphClient,
    writer: SnapshotWriter,
    page: dict[str, Any],
    notebook: dict[str, Any],
    section: dict[str, Any],
    group_path: list[str],
    order: int,
    download_resources: bool,
    normalizer: Normalizer | None,
) -> PageRecord:
    html_text = client.get_page_html(page["id"])
    rel, digest = writer.write_raw_content(page["id"], html_text.encode("utf-8"), ".html")
    record = PageRecord(
        source_backend=SourceBackend.ONENOTE_GRAPH,
        audit_role=AuditRole.CORROBORATING,
        source_page_id=page["id"],
        notebook_id=notebook.get("id", ""),
        notebook_title=notebook.get("displayName", ""),
        section_group_path=group_path,
        section_id=section.get("id", ""),
        section_title=section.get("displayName", ""),
        page_title=page.get("title", ""),
        page_level=int(page.get("level") or 0) + 1,  # Graph levels are 0-based
        page_order=int(page.get("order") or order),
        created_at=utc_iso_from_string(page.get("createdDateTime")),
        updated_at=utc_iso_from_string(page.get("lastModifiedDateTime")),
        raw_content_path=rel,
        raw_content_format=ContentFormat.HTML,
        raw_content_sha256=digest,
    )
    _capture_resources(client, writer, record, html_text, download_resources)

    if normalizer is not None:
        resource_map = {r.source_reference: r.sha256 for r in record.resources if r.sha256}
        normalized = normalizer(ContentFormat.HTML, html_text, resource_map)
        model_rel, model_digest = writer.write_semantic_model(
            record.source_page_id, normalized.semantic_model
        )
        record.semantic_model_path = model_rel
        record.semantic_model_sha256 = model_digest
        record.normalizer_version = normalized.version
        record.normalized_text = normalized.normalized_text
        record.normalized_text_sha256 = normalized.normalized_text_sha256
        record.visible_text_length = len(normalized.normalized_text)
        record.warnings.extend(normalized.warnings)
    return record


def _capture_resources(
    client: GraphClient,
    writer: SnapshotWriter,
    record: PageRecord,
    html_text: str,
    download: bool,
) -> None:
    soup = BeautifulSoup(html_text, "html.parser")
    seen: set[str] = set()
    for tag, attr, is_image in (("img", "src", True), ("object", "data", False)):
        for element in soup.find_all(tag):
            url = str(element.get("data-fullres-src") or element.get(attr) or "").strip()
            if not url or url in seen or "/onenote/resources/" not in url:
                continue
            seen.add(url)
            reference = str(element.get(attr) or url)
            filename = element.get("data-attachment")
            media_type = element.get("data-fullres-src-type") or element.get("type")
            resource = ResourceRecord(
                source_reference=reference,
                original_filename=str(filename) if filename else None,
                media_type=str(media_type) if media_type else None,
                is_image=is_image,
            )
            if download:
                try:
                    data = client.download_resource(url)
                    stored_rel, res_digest = writer.write_resource(data)
                    resource.stored_path = stored_rel
                    resource.sha256 = res_digest
                    resource.byte_length = len(data)
                    resource.status = ResourceStatus.OK
                except (GraphApiError, TransportError) as exc:
                    resource.status = ResourceStatus.UNREADABLE
                    resource.warnings.append(str(exc))
            else:
                resource.status = ResourceStatus.SKIPPED
            record.resources.append(resource)

    record.resource_hashes = sorted(r.sha256 for r in record.resources if r.sha256)
    record.image_count = sum(1 for r in record.resources if r.is_image)
    record.attachment_count = sum(1 for r in record.resources if not r.is_image)
