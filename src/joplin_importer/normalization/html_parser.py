"""HTML -> canonical semantic model.

Parsed with BeautifulSoup + the stdlib ``html.parser`` backend: no external
resources are fetched and nothing is executed. ``<script>``/``<style>``
content never reaches the model.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString, Tag

from . import model as m
from .textnorm import normalize_inline, normalize_inline_runs, normalize_text

_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP_ENTIRELY = {"script", "style", "head", "meta", "link", "title", "iframe", "object", "embed"}
_BLOCKISH = {"p", "div", "section", "article", "blockquote", "figure", "details", "summary"}


def parse_html(
    html_text: str, resource_map: dict[str, str] | None = None
) -> tuple[m.Node, list[str]]:
    """Return (semantic model, warnings)."""
    warnings: list[str] = []
    soup = BeautifulSoup(html_text or "", "html.parser")
    root = soup.body if soup.body is not None else soup
    children = _blocks_from_children(root, resource_map or {}, warnings)
    return m.document(children), warnings


# -- block collection -----------------------------------------------------------


class _InlineRun:
    """Accumulates inline text/links until flushed into a paragraph node."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []
        self.links: list[str] = []

    def append(self, text: str, href: str = "") -> None:
        self.runs.append((text, href))

    def flush(self, out: list[m.Node], *, as_heading: int | None = None) -> None:
        text, link_spans = normalize_inline_runs(self.runs)
        links = list(self.links)
        self.runs.clear()
        self.links.clear()
        if not text and not links:
            return
        if as_heading:
            out.append(m.heading(as_heading, text, links, link_spans))
        else:
            out.append(m.paragraph(text, links, link_spans))


def _blocks_from_children(
    parent: Tag, resources: dict[str, str], warnings: list[str]
) -> list[m.Node]:
    out: list[m.Node] = []
    run = _InlineRun()
    for child in parent.children:
        _dispatch(child, out, run, resources, warnings)
    run.flush(out)
    return out


def _dispatch(
    node, out: list[m.Node], run: _InlineRun, resources: dict[str, str], warnings: list[str]
) -> None:
    if isinstance(node, NavigableString):
        if node.parent and node.parent.name in _SKIP_ENTIRELY:
            return
        run.append(str(node))
        return
    if not isinstance(node, Tag):
        return  # comments, doctypes, processing instructions
    name = node.name.lower()

    if name in _SKIP_ENTIRELY:
        return
    if name in _HEADINGS:
        run.flush(out)
        heading_run = _InlineRun()
        _collect_inline(node, heading_run, out, resources, warnings)
        heading_run.flush(out, as_heading=_HEADINGS[name])
        return
    if name == "br":
        run.append("\n")
        return
    if name == "hr":
        run.flush(out)
        return
    if name in {"ul", "ol"}:
        run.flush(out)
        out.append(_parse_list(node, name == "ol", resources, warnings))
        return
    if name == "table":
        run.flush(out)
        out.append(_parse_table(node, resources, warnings))
        return
    if name == "pre":
        run.flush(out)
        code = node.find("code")
        text = (code or node).get_text()
        language = _code_language(code)
        out.append(m.code_block(normalize_text(text), language))
        return
    if name == "img":
        run.flush(out)
        out.append(_image_node(node, resources))
        return
    if name == "a":
        _collect_link(node, run, out, resources, warnings)
        return
    if name in _BLOCKISH:
        run.flush(out)
        out.extend(_blocks_from_children(node, resources, warnings))
        return
    # inline containers (span, b, i, code, ...): descend inline
    _collect_inline(node, run, out, resources, warnings)


def _collect_inline(
    tag: Tag,
    run: _InlineRun,
    out: list[m.Node],
    resources: dict[str, str],
    warnings: list[str],
    active_href: str = "",
) -> None:
    for child in tag.children:
        if isinstance(child, NavigableString):
            if child.parent and child.parent.name in _SKIP_ENTIRELY:
                continue
            run.append(str(child), active_href)
        elif isinstance(child, Tag):
            name = child.name.lower()
            if name in _SKIP_ENTIRELY:
                continue
            if name == "br":
                run.append("\n", active_href)
            elif name == "img":
                run.flush(out)
                out.append(_image_node(child, resources))
            elif name == "a":
                _collect_link(child, run, out, resources, warnings)
            else:
                _collect_inline(child, run, out, resources, warnings, active_href)


def _collect_link(
    tag: Tag, run: _InlineRun, out: list[m.Node], resources: dict[str, str], warnings: list[str]
) -> None:
    href = str(tag.get("href") or "").strip()
    text = normalize_inline(tag.get_text())
    if href.startswith(":/") and not tag.find("img"):
        # Joplin resource link used as an attachment
        run.flush(out)
        out.append(m.attachment(resources.get(href), name=text, reference=href))
        return
    if href:
        run.links.append(href)
    _collect_inline(tag, run, out, resources, warnings, href)


def _image_node(tag: Tag, resources: dict[str, str]) -> m.Node:
    src = str(tag.get("src") or "").strip()
    alt = normalize_inline(str(tag.get("alt") or ""))
    return m.image(resources.get(src), alt=alt, reference=src)


def _parse_list(tag: Tag, ordered: bool, resources: dict[str, str], warnings: list[str]) -> m.Node:
    items: list[m.Node] = []
    for li in tag.find_all("li", recursive=False):
        checked = _checkbox_state(li)
        children = _blocks_from_children(li, resources, warnings)
        if checked is not None:
            children = _strip_checkbox_marker(children)
        items.append(m.list_item(children, checked=checked))
    return m.list_block(ordered, items)


def _checkbox_state(li: Tag) -> bool | None:
    checkbox = li.find("input", attrs={"type": "checkbox"})
    if isinstance(checkbox, Tag):
        return checkbox.has_attr("checked")
    classes: list[str] = [str(c) for c in (li.get("class") or [])]
    if "task-list-item" in classes:
        return False
    return None


def _strip_checkbox_marker(children: list[m.Node]) -> list[m.Node]:
    return children


def _parse_table(tag: Tag, resources: dict[str, str], warnings: list[str]) -> m.Node:
    rows: list[list[list[m.Node]]] = []
    for tr in tag.find_all("tr"):
        # find_all is recursive. A nested row belongs only to its nearest
        # table; treating it as a row of every ancestor duplicates content.
        if tr.find_parent("table") is not tag:
            continue
        cells: list[list[m.Node]] = []
        for cell in tr.find_all(["td", "th"], recursive=False):
            cells.append(_blocks_from_children(cell, resources, warnings))
        if cells:
            rows.append(cells)
    return m.table(rows)


def _code_language(code: Tag | None) -> str | None:
    if code is None:
        return None
    for cls in code.get("class") or []:
        if cls.startswith("language-"):
            return cls.removeprefix("language-")
    return None


def extract_inline_text(
    html_fragment: str, resource_map: dict[str, str] | None = None
) -> tuple[str, list[str], list[dict[str, object]], list[m.Node]]:
    """Return text, link targets, exact link spans, and hoisted nodes.

    Used for OneNote ``one:T`` runs and inline HTML inside Markdown.
    """
    warnings: list[str] = []
    soup = BeautifulSoup(html_fragment or "", "html.parser")
    out: list[m.Node] = []
    run = _InlineRun()
    for child in soup.children:
        _dispatch(child, out, run, resource_map or {}, warnings)
    run.flush(out)
    text_parts: list[str] = []
    links: list[str] = []
    link_spans: list[dict[str, object]] = []
    hoisted: list[m.Node] = []
    for node in out:
        if node["kind"] in {"paragraph", "heading"}:
            if node["text"]:
                if text_parts:
                    offset = sum(len(part) for part in text_parts) + len(text_parts)
                else:
                    offset = 0
                text_parts.append(node["text"])
                link_spans.extend(
                    {
                        "start": int(span["start"]) + offset,
                        "end": int(span["end"]) + offset,
                        "href": str(span["href"]),
                    }
                    for span in node.get("link_spans", [])
                )
            links.extend(node.get("links", []))
        else:
            hoisted.append(node)
    return "\n".join(text_parts), links, link_spans, hoisted
