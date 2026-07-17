"""Fake Microsoft Graph OneNote server for tests (httpx.MockTransport handler)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import httpx

PAGE_HTML = """<html><head><title>{title}</title></head><body>
<div><p>{text}</p>{extra}</div>
</body></html>"""

IMG_TAG = (
    '<img src="https://graph.microsoft.com/v1.0/me/onenote/resources/{rid}/$value"'
    ' data-fullres-src="https://graph.microsoft.com/v1.0/me/onenote/resources/{rid}/$value"'
    ' data-fullres-src-type="image/png">'
)


@dataclass
class FakeGraphServer:
    token: str = "graph-token"
    notebooks: list[dict] = field(default_factory=list)
    sections: dict[str, list[dict]] = field(default_factory=dict)  # parent id -> sections
    section_groups: dict[str, list[dict]] = field(default_factory=dict)  # parent id -> groups
    pages: dict[str, list[dict]] = field(default_factory=dict)  # section id -> pages
    page_html: dict[str, str] = field(default_factory=dict)
    resources: dict[str, bytes] = field(default_factory=dict)
    page_size: int = 100
    throttle_next: int = 0  # respond 429 to this many following requests
    fail_content_for: set[str] = field(default_factory=set)
    requests: list[tuple[str, str]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        path = parsed.path.removeprefix("/v1.0")
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.requests.append((request.method, path))

        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        if request.method != "GET":
            return httpx.Response(405, json={"error": {"code": "methodNotAllowed"}})
        if self.throttle_next > 0:
            self.throttle_next -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})

        if path == "/me/onenote/notebooks":
            return self._paged(self.notebooks, params, path)
        if path.startswith("/me/onenote/notebooks/") and path.endswith("/sections"):
            return self._paged(self.sections.get(path.split("/")[4], []), params, path)
        if path.startswith("/me/onenote/notebooks/") and path.endswith("/sectionGroups"):
            return self._paged(self.section_groups.get(path.split("/")[4], []), params, path)
        if path.startswith("/me/onenote/sectionGroups/") and path.endswith("/sections"):
            return self._paged(self.sections.get(path.split("/")[4], []), params, path)
        if path.startswith("/me/onenote/sectionGroups/") and path.endswith("/sectionGroups"):
            return self._paged(self.section_groups.get(path.split("/")[4], []), params, path)
        if path.startswith("/me/onenote/sections/") and path.endswith("/pages"):
            return self._paged(self.pages.get(path.split("/")[4], []), params, path)
        if path.startswith("/me/onenote/pages/") and path.endswith("/content"):
            page_id = path.split("/")[4]
            if page_id in self.fail_content_for:
                return httpx.Response(500, json={"error": {"code": "generalException"}})
            if page_id in self.page_html:
                return httpx.Response(
                    200, text=self.page_html[page_id], headers={"Content-Type": "text/html"}
                )
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        if path.startswith("/me/onenote/resources/") and path.endswith("/$value"):
            rid = path.split("/")[4]
            if rid in self.resources:
                return httpx.Response(200, content=self.resources[rid])
            return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
        return httpx.Response(404, json={"error": {"code": f"unknown path {path}"}})

    def _paged(self, items: list[dict], params: dict, path: str) -> httpx.Response:
        skip = int(params.get("$skip", 0))
        chunk = items[skip : skip + self.page_size]
        payload: dict = {"value": chunk}
        if skip + self.page_size < len(items):
            payload["@odata.nextLink"] = (
                f"https://graph.microsoft.com/v1.0{path}?$skip={skip + self.page_size}"
            )
        return httpx.Response(200, json=payload)


def make_graph_page(page_id: str, title: str, *, level: int = 0, order: int = 0) -> dict:
    return {
        "id": page_id,
        "title": title,
        "createdDateTime": "2025-01-01T08:00:00Z",
        "lastModifiedDateTime": "2025-06-01T09:30:00Z",
        "level": level,
        "order": order,
    }
