"""Thin client over the Joplin Data API.

All traffic flows through :class:`~joplin_importer.transport.HttpTransport`,
so read-only scans structurally cannot mutate Joplin.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...transport import HttpTransport, TransportError

NOTE_FIELDS = [
    "id",
    "parent_id",
    "title",
    "body",
    "created_time",
    "updated_time",
    "user_created_time",
    "user_updated_time",
    "source_url",
    "source_application",
    "markup_language",
    "is_conflict",
    "deleted_time",
]

# body_html is accepted by some installs as an input field on note creation but
# is not guaranteed to be readable; it is probed separately.
OPTIONAL_NOTE_FIELDS = ["body_html"]

FOLDER_FIELDS = ["id", "parent_id", "title", "deleted_time", "user_data"]
RESOURCE_FIELDS = ["id", "title", "mime", "filename", "size", "ocr_text"]

_PAGE_LIMIT = 100


class JoplinApiError(RuntimeError):
    pass


class JoplinClient:
    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport
        self.capabilities: dict[str, bool] = {}

    # -- probing -------------------------------------------------------------

    def ping(self) -> str:
        response = self.transport.get("/ping")
        if response.status_code != 200 or "JoplinClipperServer" not in response.text:
            raise JoplinApiError(
                f"Joplin Data API not reachable (HTTP {response.status_code}); "
                "enable the Web Clipper service in Joplin options"
            )
        return response.text.strip()

    def probe_capabilities(self) -> dict[str, bool]:
        """Determine which optional fields this Joplin install exposes."""
        self.ping()
        for field in OPTIONAL_NOTE_FIELDS:
            try:
                response = self.transport.get(
                    "/notes", params={"fields": f"id,{field}", "limit": 1}
                )
                supported = response.status_code == 200
            except TransportError:
                # Joplin versions differ in how they reject input-only or
                # unknown fields; some return a retryable 500 instead of 4xx.
                # A failed optional probe must not prevent the base API scan.
                supported = False
            self.capabilities[f"note.{field}"] = supported
        return dict(self.capabilities)

    # -- enumeration -----------------------------------------------------------

    def iter_folders(self) -> Iterator[dict[str, Any]]:
        yield from self._paginate("/folders", {"fields": ",".join(FOLDER_FIELDS)})

    def iter_notes(self) -> Iterator[dict[str, Any]]:
        fields = list(NOTE_FIELDS)
        if self.capabilities.get("note.body_html"):
            fields.append("body_html")
        params = {
            "fields": ",".join(fields),
            "include_deleted": 1,
            "include_conflicts": 1,
        }
        yield from self._paginate("/notes", params)

    def iter_note_resources(self, note_id: str) -> Iterator[dict[str, Any]]:
        yield from self._paginate(
            f"/notes/{note_id}/resources", {"fields": ",".join(RESOURCE_FIELDS)}
        )

    def iter_resources(self) -> Iterator[dict[str, Any]]:
        """Enumerate all resources, including orphans, for safety preflights."""
        yield from self._paginate("/resources", {"fields": ",".join(RESOURCE_FIELDS)})

    def get_resource_file(self, resource_id: str) -> bytes:
        response = self.transport.get(f"/resources/{resource_id}/file")
        if response.status_code != 200:
            raise JoplinApiError(
                f"resource file download failed: {resource_id} (HTTP {response.status_code})"
            )
        return response.content

    def search(
        self, query: str, *, item_type: str = "note", fields: list[str] | None = None
    ) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "type": item_type}
        if fields:
            params["fields"] = ",".join(fields)
        yield from self._paginate("/search", params)

    def get_note(self, note_id: str, fields: list[str]) -> dict[str, Any]:
        response = self.transport.get(
            f"/notes/{note_id}", params={"fields": ",".join(fields)}
        )
        if response.status_code != 200:
            raise JoplinApiError(f"note fetch failed: {note_id} (HTTP {response.status_code})")
        return response.json()

    # -- internals ---------------------------------------------------------------

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            response = self.transport.get(
                path, params={**params, "page": page, "limit": _PAGE_LIMIT}
            )
            if response.status_code != 200:
                raise JoplinApiError(f"GET {path} page {page} failed (HTTP {response.status_code})")
            payload = response.json()
            items = payload.get("items", [])
            yield from items
            if not payload.get("has_more"):
                return
            page += 1
