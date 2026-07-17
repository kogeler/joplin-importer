"""Canonical semantic model.

A versioned, format-independent representation shared by OneNote XML, HTML,
Markdown, and mixed Markdown/HTML. Serialized as plain JSON-compatible dicts
so it hashes deterministically. Serialization-only differences (tags,
escaping, whitespace) must vanish here; genuine content differences must not.
"""

from __future__ import annotations

from typing import Any

from ..models.hashing import sha256_canonical_json

SEMANTIC_MODEL_VERSION = "2"

Node = dict[str, Any]


def document(children: list[Node]) -> Node:
    return {"kind": "doc", "version": SEMANTIC_MODEL_VERSION, "children": children}


def heading(
    level: int,
    text: str,
    links: list[str] | None = None,
    link_spans: list[dict[str, object]] | None = None,
) -> Node:
    return {
        "kind": "heading",
        "level": level,
        "text": text,
        "links": links or [],
        "link_spans": link_spans or [],
    }


def paragraph(
    text: str,
    links: list[str] | None = None,
    link_spans: list[dict[str, object]] | None = None,
) -> Node:
    return {
        "kind": "paragraph",
        "text": text,
        "links": links or [],
        "link_spans": link_spans or [],
    }


def code_block(text: str, language: str | None = None) -> Node:
    return {"kind": "code", "language": language or "", "text": text}


def list_block(ordered: bool, items: list[Node]) -> Node:
    return {"kind": "list", "ordered": ordered, "items": items}


def list_item(children: list[Node], checked: bool | None = None) -> Node:
    return {"kind": "list_item", "checked": checked, "children": children}


def table(rows: list[list[list[Node]]]) -> Node:
    # each cell is a container node holding block children; cell order is preserved
    return {"kind": "table", "rows": [[_cell(c) for c in row] for row in rows]}


def _cell(children: list[Node]) -> Node:
    return {"kind": "cell", "children": children}


def image(content_hash: str | None, alt: str = "", reference: str = "") -> Node:
    return {"kind": "image", "hash": content_hash, "alt": alt, "reference": reference}


def attachment(content_hash: str | None, name: str = "", reference: str = "") -> Node:
    return {"kind": "attachment", "hash": content_hash, "name": name, "reference": reference}


def placeholder(object_kind: str) -> Node:
    """Drawing/ink/printout/other object that has no cross-format representation."""
    return {"kind": "placeholder", "object": object_kind}


def unsupported(tag: str) -> Node:
    """A node the normalizer does not understand; recorded, never dropped."""
    return {"kind": "unsupported", "tag": tag}


def semantic_hash(model: Node) -> str:
    return sha256_canonical_json(model)


def visible_text(node: Node) -> str:
    """Deterministic visible-text projection of a model (for similarity)."""
    lines: list[str] = []
    _collect_text(node, lines)
    return "\n".join(line for line in lines if line)


def _collect_text(node: Node, lines: list[str]) -> None:
    kind = node.get("kind")
    if kind in {"heading", "paragraph", "code"}:
        if node.get("text"):
            lines.append(node["text"])
    elif kind == "table":
        for row in node.get("rows", []):
            cell_texts = [visible_text_of_children(cell.get("children", [])) for cell in row]
            lines.append("\t".join(cell_texts))
    elif kind == "list":
        for item in node.get("items", []):
            _collect_text(item, lines)
    elif kind in {"doc", "list_item", "cell"}:
        for child in node.get("children", []):
            _collect_text(child, lines)
    # image/attachment/placeholder/unsupported contribute no visible text


def visible_text_of_children(children: list[Node]) -> str:
    lines: list[str] = []
    for child in children:
        _collect_text(child, lines)
    return " ".join(line for line in lines if line)


def collect_links(node: Node) -> list[str]:
    """Distinct link targets in document order."""
    links: list[str] = []
    _collect_links(node, links)
    seen: set[str] = set()
    result: list[str] = []
    for link in links:
        if link not in seen:
            seen.add(link)
            result.append(link)
    return result


def _collect_links(node: Node, out: list[str]) -> None:
    out.extend(node.get("links", []))
    for child in node.get("children", []):
        _collect_links(child, out)
    for item in node.get("items", []):
        _collect_links(item, out)
    for row in node.get("rows", []):
        for cell in row:
            _collect_links(cell, out)


def count_kind(node: Node, kind: str) -> int:
    total = 1 if node.get("kind") == kind else 0
    for child in node.get("children", []):
        total += count_kind(child, kind)
    for item in node.get("items", []):
        total += count_kind(item, kind)
    for row in node.get("rows", []):
        for cell in row:
            total += count_kind(cell, kind)
    return total


def iter_blocks(node: Node):
    """Flat iteration over comparable top-level-ish blocks (for diffing)."""
    kind = node.get("kind")
    if kind == "doc":
        for child in node.get("children", []):
            yield from iter_blocks(child)
    else:
        yield node
