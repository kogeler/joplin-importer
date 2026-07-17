"""Parsing of OneNote page XML (GetPageContent output).

Extracts images, inserted files, and unsupported-object markers. Text/semantic
extraction is the normalizer's job; this module handles binary objects and
page metadata only. Pure Python + defusedxml: testable on any OS.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree as SafeET

from ...normalization.page_refs import assign_references

_IMAGE_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "emf": "image/emf",
    "wmf": "image/wmf",
}

#: OneNote object kinds the tool cannot convert; recorded, never dropped silently
UNSUPPORTED_TAGS = frozenset(
    {
        "InkDrawing",
        "InkParagraph",
        "InkWord",
        "MediaFile",
        "MediaIndex",
        "FutureObject",
        "EmbeddedPrintout",
        "PrintoutFile",
    }
)

MAX_CACHED_FILE_BYTES = 256 * 1024 * 1024  # refuse to slurp absurdly large caches

FileReader = Callable[[str], bytes]


@dataclass
class ParsedResource:
    kind: str  # "image" | "file"
    source_reference: str
    data: bytes | None = None
    filename: str | None = None
    media_type: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedPage:
    page_id: str
    title: str
    created_at: str | None
    updated_at: str | None
    resources: list[ParsedResource] = field(default_factory=list)
    unsupported: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def default_file_reader(path: str) -> bytes:
    """Read an InsertedFile cache path, read-only, with a size cap."""
    file_path = Path(path)
    size = file_path.stat().st_size
    if size > MAX_CACHED_FILE_BYTES:
        raise OSError(f"cached file too large ({size} bytes): {path}")
    return file_path.read_bytes()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_page_xml(xml_text: str, *, file_reader: FileReader = default_file_reader) -> ParsedPage:
    root = SafeET.fromstring(xml_text)
    page = ParsedPage(
        page_id=root.get("ID", ""),
        title=root.get("name", ""),
        created_at=root.get("dateTime"),
        updated_at=root.get("lastModifiedTime"),
    )

    refs = assign_references(root)
    for element in root.iter():
        kind = _local(element.tag)
        if kind == "Image":
            page.resources.append(_parse_image(element, refs[id(element)]))
        elif kind == "InsertedFile":
            page.resources.append(_parse_inserted_file(element, refs[id(element)], file_reader))
        elif kind in UNSUPPORTED_TAGS:
            page.unsupported[kind] = page.unsupported.get(kind, 0) + 1

    for kind, count in sorted(page.unsupported.items()):
        page.warnings.append(f"unsupported OneNote object: {kind} x{count}")
    return page


def _parse_image(element, reference: str) -> ParsedResource:
    fmt = (element.get("format") or "").lower()
    resource = ParsedResource(
        kind="image",
        source_reference=reference,
        media_type=_IMAGE_MEDIA_TYPES.get(fmt, "application/octet-stream"),
    )
    data_el = next((c for c in element if _local(c.tag) == "Data"), None)
    if data_el is not None and (data_el.text or "").strip():
        try:
            resource.data = base64.b64decode(
                re.sub(r"\s+", "", data_el.text), validate=True
            )
        except (binascii.Error, ValueError) as exc:
            resource.warnings.append(f"image base64 decode failed: {exc}")
    else:
        callback = element.get("callbackID")
        resource.warnings.append(
            "image data not embedded"
            + (f" (callbackID={callback})" if callback else " (rescan with binary data)")
        )
    return resource


def _parse_inserted_file(element, reference: str, file_reader: FileReader) -> ParsedResource:
    preferred = element.get("preferredName") or None
    resource = ParsedResource(
        kind="file",
        source_reference=reference,
        filename=preferred,
        media_type="application/octet-stream",
    )
    path_cache = element.get("pathCache")
    if path_cache:
        try:
            resource.data = file_reader(path_cache)
        except OSError as exc:
            resource.warnings.append(f"cached file unreadable: {exc}")
    else:
        resource.warnings.append("inserted file has no cached path; binary not captured")
    return resource
