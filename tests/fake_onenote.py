"""Fake OneNote COM API implementing the OneNoteApi protocol for tests."""

from __future__ import annotations

from pathlib import Path

from joplin_importer.adapters.onenote_com.api import (
    OneNoteApiError,
    OneNoteProcessUnavailableError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "onenote"

ONE_NS = "http://schemas.microsoft.com/office/onenote/2013/onenote"

# 1x1 transparent PNG
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def page_xml(
    page_id: str,
    title: str,
    *,
    body_text: str = "",
    with_image: bool = False,
    with_file_path: str | None = None,
    with_ink: bool = False,
    created: str = "2025-01-01T08:00:00.000Z",
    modified: str = "2025-06-01T09:30:00.000Z",
) -> str:
    parts = [
        f'<one:Page xmlns:one="{ONE_NS}" ID="{page_id}" name="{title}" '
        f'dateTime="{created}" lastModifiedTime="{modified}" pageLevel="1">',
        f"<one:Title><one:OE><one:T><![CDATA[{title}]]></one:T></one:OE></one:Title>",
        "<one:Outline><one:OEChildren>",
    ]
    if body_text:
        parts.append(f"<one:OE><one:T><![CDATA[{body_text}]]></one:T></one:OE>")
    if with_image:
        parts.append(
            f'<one:OE><one:Image format="png"><one:Size width="1" height="1"/>'
            f"<one:Data>{PNG_BASE64}</one:Data></one:Image></one:OE>"
        )
    if with_file_path is not None:
        parts.append(
            f'<one:OE><one:InsertedFile pathCache="{with_file_path}" '
            f'preferredName="report.pdf"/></one:OE>'
        )
    if with_ink:
        parts.append("<one:OE><one:InkDrawing/></one:OE>")
    parts.append("</one:OEChildren></one:Outline></one:Page>")
    return "".join(parts)


class FakeOneNoteApi:
    """In-memory OneNoteApi: hierarchy fixture + per-page XML."""

    def __init__(
        self,
        hierarchy_xml: str | None = None,
        pages: dict[str, str] | None = None,
        fail_pages: set[str] | None = None,
        process_crash_pages: set[str] | None = None,
    ) -> None:
        self.hierarchy_xml = hierarchy_xml or (FIXTURES / "hierarchy.xml").read_text(
            encoding="utf-8"
        )
        self.pages = pages or {}
        self.fail_pages = fail_pages or set()
        self.process_crash_pages = process_crash_pages or set()
        self.calls: list[str] = []

    def get_hierarchy(self) -> str:
        self.calls.append("GetHierarchy")
        return self.hierarchy_xml

    def get_page_content(self, page_id: str, *, include_binary: bool = True) -> str:
        self.calls.append(f"GetPageContent:{page_id}")
        if page_id in self.process_crash_pages:
            raise OneNoteProcessUnavailableError(
                f"GetPageContent lost the OneNote COM process for {page_id}"
            )
        if page_id in self.fail_pages:
            raise OneNoteApiError(f"COM error 0x80042010 for {page_id}")
        if page_id not in self.pages:
            raise OneNoteApiError(f"unknown page {page_id}")
        return self.pages[page_id]


def default_pages(tmp_file: str | None = None) -> dict[str, str]:
    """Page XML bodies matching tests/fixtures/onenote/hierarchy.xml."""
    return {
        "{page-tasks-1}": page_xml(
            "{page-tasks-1}", "Задачи недели", body_text="Сделать отчёт", with_image=True
        ),
        "{page-tasks-2}": page_xml("{page-tasks-2}", "Subtask detail", body_text="Details here"),
        "{page-tasks-3}": page_xml("{page-tasks-3}", "Untitled Page"),
        "{page-old-1}": page_xml(
            "{page-old-1}",
            "Old note",
            body_text="Archived",
            with_file_path=tmp_file,
            with_ink=True,
        )
        if tmp_file
        else page_xml("{page-old-1}", "Old note", body_text="Archived", with_ink=True),
        "{page-trash-1}": page_xml("{page-trash-1}", "Deleted page", body_text="trashed"),
    }
