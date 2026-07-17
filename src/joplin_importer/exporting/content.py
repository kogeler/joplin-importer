"""Safe deterministic content rendering for the supported full exporter."""

from __future__ import annotations

import html

from ..models import PageRecord
from ..normalization.model import Node

RESOURCE_PLACEHOLDER = "joplin-importer-resource://{sha256}"


def build_export_html(
    page: PageRecord,
    semantic_model: Node,
    *,
    plan_id: str,
    action_id: str,
) -> str:
    """Render one managed-export note without recovery-only banners."""
    # Ownership is stored on the managed folder tree, while source identity is
    # stored in Joplin metadata. Keeping it out of the body preserves the
    # user's visible and normalized content.
    _ = (page, plan_id, action_id)
    parts = [_render_block(child) for child in semantic_model.get("children", [])]
    return "\n".join(part for part in parts if part)


def _render_block(node: Node) -> str:
    kind = node.get("kind")
    if kind in {"placeholder", "unsupported"}:
        return ""
    if kind == "heading":
        level = min(max(int(node.get("level", 1)), 1), 6)
        return f"<h{level}>{_inline(node)}</h{level}>"
    if kind == "paragraph":
        return f"<p>{_inline(node)}</p>"
    if kind == "code":
        language = _attr(node.get("language") or "")
        cls = f' class="language-{language}"' if language else ""
        return f"<pre><code{cls}>{_esc(node.get('text', ''))}</code></pre>"
    if kind == "list":
        tag = "ol" if node.get("ordered") else "ul"
        items = "".join(_render_list_item(item) for item in node.get("items", []))
        return f"<{tag}>{items}</{tag}>"
    if kind == "table":
        rows = []
        for row in node.get("rows", []):
            cells = "".join(
                "<td>"
                + "".join(_render_block(child) for child in cell.get("children", []))
                + "</td>"
                for cell in row
            )
            rows.append(f"<tr>{cells}</tr>")
        return "<table>" + "".join(rows) + "</table>"
    if kind in {"image", "attachment"} and node.get("hash"):
        return _resource_ref(node, is_image=kind == "image")
    return ""


def _render_list_item(item: Node) -> str:
    checked = item.get("checked")
    prefix = ""
    if checked is True:
        prefix = "<input type='checkbox' checked disabled> "
    elif checked is False:
        prefix = "<input type='checkbox' disabled> "
    body = "".join(_render_block(child) for child in item.get("children", []))
    return f"<li>{prefix}{body}</li>"


def _resource_ref(node: Node, *, is_image: bool) -> str:
    placeholder = RESOURCE_PLACEHOLDER.format(sha256=node["hash"])
    if is_image:
        return f'<img src="{placeholder}" alt="{_attr(node.get("alt") or "")}">'
    name = _esc(node.get("name") or "attachment")
    return f'<p><a href="{placeholder}">{name}</a></p>'


def _inline(node: Node) -> str:
    text = str(node.get("text", ""))
    spans = node.get("link_spans") or []
    if not spans:
        # Older semantic models did not retain label-to-target association.
        # Preserve their visible text without appending another URL copy.
        return _esc(text).replace("\n", "<br>")

    rendered: list[str] = []
    cursor = 0
    for span in spans:
        try:
            start = int(span["start"])
            end = int(span["end"])
            href = str(span["href"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < cursor or end <= start or end > len(text):
            continue
        rendered.append(_esc(text[cursor:start]))
        label = _esc(text[start:end])
        if _safe_url(href):
            rendered.append(f'<a href="{_attr(href)}">{label}</a>')
        else:
            rendered.append(label)
        cursor = end
    rendered.append(_esc(text[cursor:]))
    return "".join(rendered).replace("\n", "<br>")


def _safe_url(url: str) -> bool:
    return url.strip().lower().startswith(
        ("http://", "https://", "onenote:", ":/", "joplin://")
    )


def _esc(value: str) -> str:
    return html.escape(str(value), quote=False)


def _attr(value: str) -> str:
    return html.escape(str(value), quote=True)
