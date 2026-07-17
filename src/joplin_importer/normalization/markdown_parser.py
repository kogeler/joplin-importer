"""Markdown (with possible inline HTML) -> canonical semantic model.

Uses a CommonMark parser (markdown-it-py) with tables enabled and Joplin
specifics handled explicitly: ``:/<id>`` resource links, ``- [x]`` task
lists, fenced code, internal note links, and inline HTML fragments.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from . import model as m
from .html_parser import extract_inline_text, parse_html
from .textnorm import normalize_inline_runs, normalize_text

_TASK_RE = re.compile(r"^\[([ xX])\]\s+")

_md = MarkdownIt("commonmark").enable("table").enable("strikethrough")


def parse_markdown(
    md_text: str, resource_map: dict[str, str] | None = None
) -> tuple[m.Node, list[str]]:
    """Return (semantic model, warnings)."""
    warnings: list[str] = []
    resources = resource_map or {}
    tokens = _md.parse(md_text or "")
    tree = SyntaxTreeNode(tokens)
    children = _blocks(tree, resources, warnings)
    return m.document(children), warnings


def _blocks(parent: SyntaxTreeNode, resources: dict[str, str], warnings: list[str]) -> list[m.Node]:
    out: list[m.Node] = []
    for node in parent.children:
        out.extend(_block(node, resources, warnings))
    return out


def _block(node: SyntaxTreeNode, resources: dict[str, str], warnings: list[str]) -> list[m.Node]:
    ntype = node.type
    if ntype == "heading":
        level = int(node.tag[1]) if len(node.tag) == 2 else 1
        text, links, link_spans, hoisted = _inline(node, resources, warnings)
        return [m.heading(level, text, links, link_spans), *hoisted]
    if ntype == "paragraph":
        text, links, link_spans, hoisted = _inline(node, resources, warnings)
        result: list[m.Node] = []
        if text or links:
            result.append(m.paragraph(text, links, link_spans))
        result.extend(hoisted)
        return result
    if ntype in {"fence", "code_block"}:
        language = (node.info or "").strip() or None if ntype == "fence" else None
        return [m.code_block(normalize_text(node.content), language)]
    if ntype in {"bullet_list", "ordered_list"}:
        return [_list(node, ntype == "ordered_list", resources, warnings)]
    if ntype == "table":
        return [_table(node, resources, warnings)]
    if ntype == "blockquote":
        return _blocks(node, resources, warnings)
    if ntype == "html_block":
        html_model, html_warnings = parse_html(node.content, resources)
        warnings.extend(html_warnings)
        return list(html_model["children"])
    if ntype == "hr":
        return []
    if ntype == "inline":  # top-level inline (shouldn't normally happen)
        text, links, link_spans, hoisted = _inline_from(node, resources, warnings)
        return (
            [m.paragraph(text, links, link_spans)] if text or links else []
        ) + hoisted
    warnings.append(f"unsupported markdown node: {ntype}")
    return [m.unsupported(ntype)]


def _list(
    node: SyntaxTreeNode, ordered: bool, resources: dict[str, str], warnings: list[str]
) -> m.Node:
    items: list[m.Node] = []
    for item in node.children:
        if item.type != "list_item":
            continue
        children = _blocks(item, resources, warnings)
        checked = _extract_task_state(children)
        items.append(m.list_item(children, checked=checked))
    return m.list_block(ordered, items)


def _extract_task_state(children: list[m.Node]) -> bool | None:
    """Detect a leading '[ ] ' / '[x] ' marker in the first paragraph."""
    for child in children:
        if child["kind"] != "paragraph":
            return None
        match = _TASK_RE.match(child["text"])
        if not match:
            return None
        checked = match.group(1).lower() == "x"
        child["text"] = child["text"][match.end() :]
        return checked
    return None


def _table(node: SyntaxTreeNode, resources: dict[str, str], warnings: list[str]) -> m.Node:
    rows: list[list[list[m.Node]]] = []
    for section in node.children:  # thead / tbody
        for tr in section.children:
            cells: list[list[m.Node]] = []
            for cell in tr.children:  # th / td
                text, links, link_spans, hoisted = _inline(cell, resources, warnings)
                blocks: list[m.Node] = []
                if text or links:
                    blocks.append(m.paragraph(text, links, link_spans))
                blocks.extend(hoisted)
                cells.append(blocks)
            rows.append(cells)
    return m.table(rows)


def _inline(container: SyntaxTreeNode, resources: dict[str, str], warnings: list[str]):
    for child in container.children:
        if child.type == "inline":
            return _inline_from(child, resources, warnings)
    return "", [], []


def _inline_from(inline: SyntaxTreeNode, resources: dict[str, str], warnings: list[str]):
    runs: list[tuple[str, str]] = []
    links: list[str] = []
    hoisted: list[m.Node] = []

    def append_fragment(
        text: str, link_spans: list[dict[str, object]], active_href: str = ""
    ) -> None:
        cursor = 0
        for span in link_spans:
            start = int(str(span["start"]))
            end = int(str(span["end"]))
            if start > cursor:
                runs.append((text[cursor:start], active_href))
            runs.append((text[start:end], str(span["href"])))
            cursor = end
        if cursor < len(text):
            runs.append((text[cursor:], active_href))

    def walk(node: SyntaxTreeNode, active_href: str = "") -> None:
        ntype = node.type
        if ntype == "text":
            runs.append((node.content, active_href))
        elif ntype == "code_inline":
            runs.append((node.content, active_href))
        elif ntype == "softbreak":
            runs.append((" ", active_href))
        elif ntype == "hardbreak":
            runs.append(("\n", active_href))
        elif ntype == "image":
            src = str(node.attrs.get("src", "")).strip()
            alt = "".join(
                c.content for c in node.children if c.type == "text"
            )
            hoisted.append(m.image(resources.get(src), alt=normalize_text(alt), reference=src))
        elif ntype == "link":
            href = str(node.attrs.get("href", "")).strip()
            inner_text = _plain_text(node)
            if href.startswith(":/") and not any(c.type == "image" for c in node.children):
                hoisted.append(
                    m.attachment(
                        resources.get(href), name=normalize_text(inner_text), reference=href
                    )
                )
                return  # attachment link text is the attachment name, not body text
            if href:
                links.append(href)
            for child in node.children:
                walk(child, href or active_href)
        elif ntype == "html_inline":
            text, frag_links, frag_spans, frag_nodes = extract_inline_text(
                node.content, resources
            )
            if node.content.lower().startswith("<br"):
                runs.append(("\n", active_href))
            elif text:
                append_fragment(text, frag_spans, active_href)
            links.extend(frag_links)
            hoisted.extend(frag_nodes)
        else:
            for child in node.children:
                walk(child, active_href)

    for child in inline.children:
        walk(child)
    text, link_spans = normalize_inline_runs(runs)
    return text, links, link_spans, hoisted


def _plain_text(node: SyntaxTreeNode) -> str:
    if node.type == "text":
        return node.content
    return "".join(_plain_text(c) for c in node.children)
