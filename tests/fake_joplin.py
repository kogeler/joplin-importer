"""Recording fake Joplin Data API server for automated tests.

Implemented as an httpx.MockTransport handler: no sockets, fully in-process.
Records every request so tests can assert that only allowed read requests
were sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx


@dataclass
class FakeJoplinServer:
    folders: list[dict[str, Any]] = field(default_factory=list)
    notes: list[dict[str, Any]] = field(default_factory=list)
    note_resources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    resource_bytes: dict[str, bytes] = field(default_factory=dict)
    token: str = "tok-123"
    page_size: int = 100  # server-side page size, lower it to force pagination
    fail_resources_for: set[str] = field(default_factory=set)
    unsupported_note_fields: set[str] = field(default_factory=set)
    fail_note_creations: int = 0  # fail this many POST /notes calls (interruption tests)
    requests: list[tuple[str, str]] = field(default_factory=list)
    created_notes: list[dict[str, Any]] = field(default_factory=list)
    created_folders: list[dict[str, Any]] = field(default_factory=list)
    created_resources: list[dict[str, Any]] = field(default_factory=list)

    # -- wiring ----------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        path = parsed.path
        self.requests.append((request.method, path))

        if params.get("token") != self.token:
            return httpx.Response(403, json={"error": "invalid token"})

        if request.method == "GET":
            return self._get(path, params)
        return self._mutate(request, path, params)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def mutating_requests(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p in self.requests if m != "GET"]

    # -- GET ---------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        if path == "/ping":
            return httpx.Response(200, text="JoplinClipperServer")
        if path == "/search":
            return self._search(params)
        if path == "/folders":
            return self._paged([self._project(f, params) for f in self.folders], params)
        if path == "/notes":
            requested_fields = set(params.get("fields", "").split(","))
            if requested_fields & self.unsupported_note_fields:
                return httpx.Response(500, json={"error": "unsupported field"})
            items = [*self.notes, *self.created_notes]
            if params.get("include_deleted") != "1":
                items = [n for n in items if not n.get("deleted_time")]
            if params.get("include_conflicts") != "1":
                items = [n for n in items if not n.get("is_conflict")]
            return self._paged([self._project(n, params) for n in items], params)
        if path == "/resources":
            return self._paged(
                [self._project(resource, params) for resource in self.created_resources],
                params,
            )
        if path.startswith("/notes/") and path.endswith("/resources"):
            note_id = path.split("/")[2]
            if note_id in self.fail_resources_for:
                return httpx.Response(500, json={"error": "internal error"})
            items = self.note_resources.get(note_id, [])
            return self._paged([self._project(r, params) for r in items], params)
        if path.startswith("/notes/"):
            note_id = path.split("/")[2]
            for note in self.notes + self.created_notes:
                if note["id"] == note_id:
                    return httpx.Response(200, json=self._project(note, params))
            return httpx.Response(404, json={"error": "not found"})
        if path.startswith("/resources/") and path.endswith("/file"):
            res_id = path.split("/")[2]
            if res_id in self.resource_bytes:
                return httpx.Response(200, content=self.resource_bytes[res_id])
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(404, json={"error": f"unknown path {path}"})

    def _search(self, params: dict[str, str]) -> httpx.Response:
        query = params.get("query", "").strip('"')
        item_type = params.get("type", "note")
        items: list[dict[str, Any]] = []
        if item_type == "note":
            for note in self.notes + self.created_notes:
                haystack = "\n".join(
                    str(note.get(k, "")) for k in ("title", "body", "body_html")
                )
                if query and query in haystack:
                    items.append(self._project(note, params))
        elif item_type == "resource":
            for res in self.created_resources:
                if query and query in res.get("title", ""):
                    items.append(self._project(res, params))
        return self._paged(items, params)

    # -- mutations (only reachable when a test enables writes) --------------------

    def _mutate(self, request: httpx.Request, path: str, params: dict[str, str]) -> httpx.Response:
        if request.method == "POST" and path == "/resources":
            return self._create_resource(request)
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        if request.method == "POST" and path == "/notes":
            if self.fail_note_creations > 0:
                self.fail_note_creations -= 1
                return httpx.Response(500, json={"error": "simulated interruption"})
            note = {
                "id": f"created-{len(self.created_notes) + 1:032x}"[-32:],
                "deleted_time": 0,
                "is_conflict": 0,
                "markup_language": 1,
                "created_time": 1_750_000_000_000,
                "updated_time": 1_750_000_000_000,
                **body,
            }
            if "body_html" in note and "body" not in note:
                # emulate Joplin's HTML import: the note gets a markdown body
                note["body"] = note["body_html"]
            self.created_notes.append(note)
            resource_ids = set(re.findall(r":/([0-9a-f]{32})", note.get("body", "")))
            self.note_resources[note["id"]] = [
                resource
                for resource in self.created_resources
                if resource["id"] in resource_ids
            ]
            return httpx.Response(200, json=note)
        if request.method == "POST" and path == "/folders":
            folder = {
                "id": f"folder-{len(self.created_folders) + 1:032x}"[-32:],
                "deleted_time": 0,
                **body,
            }
            self.created_folders.append(folder)
            self.folders.append(folder)
            return httpx.Response(200, json=folder)
        if request.method == "PUT" and path.startswith("/notes/"):
            note_id = path.split("/")[2]
            for note in self.notes:
                if note["id"] == note_id:
                    note.update(body)
                    return httpx.Response(200, json=note)
            return httpx.Response(404, json={"error": "not found"})
        if request.method == "PUT" and path.startswith("/folders/"):
            folder_id = path.split("/")[2]
            for folder in self.folders:
                if folder["id"] == folder_id:
                    folder.update(body)
                    return httpx.Response(200, json=folder)
            return httpx.Response(404, json={"error": "not found"})
        if request.method == "DELETE" and path.startswith("/folders/"):
            folder_id = path.split("/")[2]
            descendants = {folder_id}
            changed = True
            while changed:
                changed = False
                for folder in self.folders:
                    if folder.get("parent_id") in descendants and folder["id"] not in descendants:
                        descendants.add(folder["id"])
                        changed = True
            found = False
            for folder in self.folders:
                if folder["id"] in descendants:
                    folder["deleted_time"] = 1
                    found = True
            for note in [*self.notes, *self.created_notes]:
                if note.get("parent_id") in descendants:
                    note["deleted_time"] = 1
            return httpx.Response(200 if found else 404, json={"id": folder_id})
        return httpx.Response(405, json={"error": "method not allowed"})

    def _create_resource(self, request: httpx.Request) -> httpx.Response:
        """Minimal multipart parsing: extract the props JSON and data size."""
        content_type = request.headers.get("content-type", "")
        props: dict[str, Any] = {}
        data_len = 0
        data_bytes = b""
        if "boundary=" in content_type:
            boundary = content_type.split("boundary=")[1].encode()
            for part in request.content.split(b"--" + boundary):
                if b'name="props"' in part:
                    payload = part.split(b"\r\n\r\n", 1)[-1].strip().rstrip(b"-").strip()
                    try:
                        props = json.loads(payload.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        props = {}
                elif b'name="data"' in part:
                    data_bytes = part.split(b"\r\n\r\n", 1)[-1].rstrip(b"\r\n-")
                    data_len = len(data_bytes)
        res = {
            "id": f"{len(self.created_resources) + 1:032x}",
            "title": props.get("title", ""),
            "filename": props.get("filename", ""),
            "size": data_len,
        }
        self.created_resources.append(res)
        self.resource_bytes[res["id"]] = data_bytes
        return httpx.Response(200, json=res)

    # -- helpers -------------------------------------------------------------------

    def _paged(self, items: list[dict[str, Any]], params: dict[str, str]) -> httpx.Response:
        page = int(params.get("page", 1))
        limit = min(int(params.get("limit", 100)), self.page_size)
        start = (page - 1) * limit
        chunk = items[start : start + limit]
        return httpx.Response(
            200, json={"items": chunk, "has_more": start + limit < len(items)}
        )

    @staticmethod
    def _project(item: dict[str, Any], params: dict[str, str]) -> dict[str, Any]:
        fields = params.get("fields")
        if not fields:
            return dict(item)
        wanted = [f.strip() for f in fields.split(",")]
        return {k: item.get(k) for k in wanted if k in item}


def make_note(
    note_id: str,
    title: str = "Note",
    body: str = "",
    parent_id: str = "folder-1",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": note_id,
        "parent_id": parent_id,
        "title": title,
        "body": body,
        # created_time is when the importer created the note (the import run);
        # keep it after the synthetic OneNote pages' creation dates (2023-2025)
        "created_time": 1_750_000_000_000,  # 2025-06-15T15:06:40Z
        "updated_time": 1_750_000_100_000,
        "user_created_time": 1_600_000_000_000,
        "user_updated_time": 1_600_000_100_000,
        "source_url": "",
        "source_application": "",
        "markup_language": 1,
        "is_conflict": 0,
        "deleted_time": 0,
        **extra,
    }
