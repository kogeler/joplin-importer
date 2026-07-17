"""Thin client over Microsoft Graph OneNote v1.0.

Follows every ``@odata.nextLink``; retries/backoff live in the shared
transport. Graph is a corroborating source: an omission here must never by
itself prove that content is absent from OneNote.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...transport import HttpTransport

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

PAGE_FIELDS = (
    "id,title,createdDateTime,lastModifiedDateTime,level,order,"
    "parentSection,parentNotebook"
)


class GraphApiError(RuntimeError):
    pass


class GraphClient:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    def iter_notebooks(self) -> Iterator[dict[str, Any]]:
        yield from self._paged("/me/onenote/notebooks")

    def iter_section_groups(self, parent_url_path: str) -> Iterator[dict[str, Any]]:
        yield from self._paged(parent_url_path)

    def iter_sections(self, url_path: str) -> Iterator[dict[str, Any]]:
        yield from self._paged(url_path)

    def iter_section_pages(self, section_id: str) -> Iterator[dict[str, Any]]:
        yield from self._paged(
            f"/me/onenote/sections/{section_id}/pages",
            params={"$select": PAGE_FIELDS, "$top": 100, "$orderby": "order"},
        )

    def get_page_html(self, page_id: str) -> str:
        response = self.transport.get(f"/me/onenote/pages/{page_id}/content")
        if response.status_code != 200:
            raise GraphApiError(
                f"page content fetch failed: {page_id} (HTTP {response.status_code})"
            )
        return response.text

    def download_resource(self, url: str) -> bytes:
        """Download a Graph resource ($value URL found in page HTML)."""
        response = self.transport.get(url)
        if response.status_code != 200:
            raise GraphApiError(f"resource download failed (HTTP {response.status_code})")
        return response.content

    def _paged(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        url: str | None = path
        first = True
        while url:
            response = self.transport.get(url, params=params if first else None)
            if response.status_code != 200:
                raise GraphApiError(f"GET {path} failed (HTTP {response.status_code})")
            payload = response.json()
            yield from payload.get("value", [])
            url = payload.get("@odata.nextLink")
            first = False
