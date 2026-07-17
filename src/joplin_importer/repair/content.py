"""Recovery note content generation.

The body is built from the canonical semantic model, so it is safe by
construction: every text fragment is HTML-escaped, no scripts/event handlers/
external loads can appear, and resources are referenced as ``:/<resource-id>``
placeholders rewritten to real Joplin resource IDs at apply time.
"""

from __future__ import annotations

import html

from ..models import PageRecord
from ..normalization.model import Node

#: placeholder scheme: resource content hash, replaced by :/<joplin-id> at apply
RESOURCE_PLACEHOLDER = "joplin-importer-resource://{sha256}"


def build_export_html(
    page: PageRecord,
    semantic_model: Node,
    *,
    plan_id: str,
    action_id: str,
) -> str:
    """Render a plain, deterministic page for a full managed export.

    Unlike :func:`build_recovery_html`, this deliberately adds no recovery
    banner or audit metadata to the visible note.  The HTML comment and hidden
    marker provide provenance and resume/idempotency keys without changing the
    user's content.
    """
    # Ownership lives on the managed folder tree and note identity lives in
    # source_url. Keeping provenance out of the body ensures an export does not
    # change the user's visible or normalized text.
    _ = (page, plan_id, action_id)
    parts = [_render_export_block(child) for child in semantic_model.get("children", [])]
    return "\n".join(part for part in parts if part)


def _render_export_block(node: Node) -> str:
    """Render content without recovery-only warning prose."""
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
        items = "".join(_render_export_list_item(item) for item in node.get("items", []))
        return f"<{tag}>{items}</{tag}>"
    if kind == "table":
        rows = []
        for row in node.get("rows", []):
            cells = "".join(
                "<td>"
                + "".join(_render_export_block(child) for child in cell.get("children", []))
                + "</td>"
                for cell in row
            )
            rows.append(f"<tr>{cells}</tr>")
        return "<table>" + "".join(rows) + "</table>"
    if kind in {"image", "attachment"}:
        if not node.get("hash"):
            return ""
        return _resource_ref(node, is_image=kind == "image")
    return ""


def _render_export_list_item(item: Node) -> str:
    checked = item.get("checked")
    prefix = ""
    if checked is True:
        prefix = "<input type='checkbox' checked disabled> "
    elif checked is False:
        prefix = "<input type='checkbox' disabled> "
    body = "".join(_render_export_block(child) for child in item.get("children", []))
    return f"<li>{prefix}{body}</li>"


def build_recovery_html(
    page: PageRecord,
    semantic_model: Node,
    *,
    source_backend: str,
    plan_created_at_utc: str,
    action_id: str = "",
    warnings: list[str] | None = None,
) -> str:
    """Deterministic recovery HTML for one source page."""
    esc = _esc
    parts: list[str] = []
    marker = (
        f"joplin-importer:onenote_page_id={_attr(page.source_page_id)} "
        "joplin-importer:recovery=1"
    )
    if action_id:
        # the idempotency marker: apply searches for this before creating
        marker += f" joplin-importer:action_id={_attr(action_id)}"
    parts.append(f"<!-- {marker} -->")
    if action_id:
        parts.append(
            f"<p style='display:none'>joplin-importer:action_id={esc(action_id)}</p>"
        )
    parts.append("<div>")
    parts.append("<p><em>Recovered from OneNote by joplin-importer.</em></p>")
    parts.append("<ul>")
    parts.append(f"<li>Original title: {esc(page.page_title)}</li>")
    path = " / ".join([page.notebook_title, *page.section_group_path, page.section_title])
    parts.append(f"<li>Original location: {esc(path)}</li>")
    if page.created_at:
        parts.append(f"<li>Original created: {esc(page.created_at)}</li>")
    if page.updated_at:
        parts.append(f"<li>Original modified: {esc(page.updated_at)}</li>")
    parts.append(f"<li>Source backend: {esc(source_backend)}</li>")
    parts.append(f"<li>Recovery plan created: {esc(plan_created_at_utc)}</li>")
    parts.append(f"<li>OneNote page ID: {esc(page.source_page_id)}</li>")
    parts.append("</ul>")

    all_warnings = [*(warnings or []), *page.warnings]
    if all_warnings:
        parts.append("<div style='border:1px solid #c00; padding:8px;'>")
        parts.append("<p><strong>Recovery warnings:</strong></p><ul>")
        for warning in all_warnings:
            parts.append(f"<li>{esc(warning)}</li>")
        parts.append("</ul></div>")

    parts.append("<hr>")
    for child in semantic_model.get("children", []):
        parts.append(_render_block(child))
    parts.append("</div>")
    return "\n".join(part for part in parts if part)


def _render_block(node: Node) -> str:
    kind = node.get("kind")
    if kind == "heading":
        level = min(max(int(node.get("level", 1)), 1), 6)
        return f"<h{level}>{_inline(node)}</h{level}>"
    if kind == "paragraph":
        return f"<p>{_inline(node)}</p>"
    if kind == "code":
        language = _attr(node.get("language") or "")
        cls = f" class=\"language-{language}\"" if language else ""
        return f"<pre><code{cls}>{_esc(node.get('text', ''))}</code></pre>"
    if kind == "list":
        tag = "ol" if node.get("ordered") else "ul"
        items = "".join(_render_list_item(item) for item in node.get("items", []))
        return f"<{tag}>{items}</{tag}>"
    if kind == "table":
        rows = []
        for row in node.get("rows", []):
            cells = "".join(
                "<td>" + "".join(_render_block(c) for c in cell.get("children", [])) + "</td>"
                for cell in row
            )
            rows.append(f"<tr>{cells}</tr>")
        return "<table>" + "".join(rows) + "</table>"
    if kind == "image":
        return _resource_ref(node, is_image=True)
    if kind == "attachment":
        return _resource_ref(node, is_image=False)
    if kind == "placeholder":
        return (
            "<p><strong>[Unsupported OneNote object: "
            f"{_esc(node.get('object', 'unknown'))} — original content could not be "
            "converted]</strong></p>"
        )
    if kind == "unsupported":
        return (
            f"<p><strong>[Unrecognized element {_esc(node.get('tag', '?'))} was "
            "preserved as a marker]</strong></p>"
        )
    return ""


def _render_list_item(item: Node) -> str:
    checked = item.get("checked")
    prefix = ""
    if checked is True:
        prefix = "<input type='checkbox' checked disabled> "
    elif checked is False:
        prefix = "<input type='checkbox' disabled> "
    body = "".join(_render_block(c) for c in item.get("children", []))
    return f"<li>{prefix}{body}</li>"


def _resource_ref(node: Node, *, is_image: bool) -> str:
    digest = node.get("hash")
    if not digest:
        kind = "image" if is_image else "attachment"
        return (
            f"<p><strong>[Missing {kind}: content was not recoverable from the "
            "source]</strong></p>"
        )
    placeholder = RESOURCE_PLACEHOLDER.format(sha256=digest)
    if is_image:
        alt = _attr(node.get("alt") or "")
        return f'<img src="{placeholder}" alt="{alt}">'
    name = _esc(node.get("name") or "attachment")
    return f'<p><a href="{placeholder}">{name}</a></p>'


def _inline(node: Node) -> str:
    text = str(node.get("text", ""))
    spans = node.get("link_spans") or []
    if not spans:
        # Version-1 semantic models did not retain the label-to-target
        # association. Preserve their visible text without appending a second
        # copy of every URL.
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
    lowered = url.strip().lower()
    return lowered.startswith(("http://", "https://", "onenote:", ":/", "joplin://"))


def _esc(value: str) -> str:
    return html.escape(str(value), quote=False)


def _attr(value: str) -> str:
    return html.escape(str(value), quote=True)
