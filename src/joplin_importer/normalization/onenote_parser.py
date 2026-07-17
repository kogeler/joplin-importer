"""OneNote 2013 page XML -> canonical semantic model.

The page title is excluded from the body model (titles are compared as
metadata). Inline ``one:T`` runs contain HTML-ish markup and are parsed with
the safe HTML inline extractor. Images/files are numbered in document order
(``image:N`` / ``file:N``) consistently with the COM page parser so resource
hashes resolve.
"""

from __future__ import annotations

from defusedxml import ElementTree as SafeET

from . import model as m
from .html_parser import extract_inline_text
from .page_refs import assign_references
from .textnorm import normalize_text

_UNSUPPORTED_OBJECTS = {
    "InkDrawing": "ink",
    "InkParagraph": "ink",
    "InkWord": "ink",
    "MediaFile": "media",
    "FutureObject": "unknown-object",
    "EmbeddedPrintout": "printout",
    "PrintoutFile": "printout",
}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def parse_onenote_xml(
    xml_text: str, resource_map: dict[str, str] | None = None
) -> tuple[m.Node, list[str]]:
    """Return (semantic model, warnings)."""
    warnings: list[str] = []
    resources = resource_map or {}
    try:
        root = SafeET.fromstring(xml_text or "<empty/>")
    except SafeET.ParseError as exc:
        warnings.append(f"onenote xml parse error: {exc}")
        return m.document([]), warnings

    refs = assign_references(root)
    children: list[m.Node] = []
    for element in list(root):
        kind = _local(element.tag)
        if kind == "Title":
            continue  # title is metadata, not body content
        if kind == "Outline":
            children.extend(_parse_oe_children(element, refs, resources, warnings))
    return m.document(children), warnings


def _parse_oe_children(container, refs, resources, warnings) -> list[m.Node]:
    """Parse every one:OEChildren under *container* into block nodes."""
    out: list[m.Node] = []
    for child in list(container):
        if _local(child.tag) == "OEChildren":
            out.extend(_parse_oes(list(child), refs, resources, warnings))
    return out


def _parse_oes(oes, refs, resources, warnings) -> list[m.Node]:
    """Parse a sequence of one:OE elements, grouping list items."""
    out: list[m.Node] = []
    pending_list: list[m.Node] | None = None
    pending_ordered = False

    def flush_list() -> None:
        nonlocal pending_list
        if pending_list:
            out.append(m.list_block(pending_ordered, pending_list))
        pending_list = None

    for oe in oes:
        if _local(oe.tag) != "OE":
            continue
        is_list, ordered = _list_marker(oe)
        checked = _checklist_state(oe)
        blocks = _parse_oe(oe, refs, resources, warnings)
        if is_list or checked is not None:
            if pending_list is None or (is_list and ordered != pending_ordered):
                flush_list()
                pending_list = []
                pending_ordered = ordered
            pending_list.append(m.list_item(blocks, checked=checked))
        else:
            flush_list()
            out.extend(blocks)
    flush_list()
    return out


def _parse_oe(oe, refs, resources, warnings) -> list[m.Node]:
    blocks: list[m.Node] = []
    for child in list(oe):
        kind = _local(child.tag)
        if kind == "T":
            text, links, link_spans, hoisted = extract_inline_text(
                child.text or "", resources
            )
            if text or links:
                blocks.append(m.paragraph(text, links, link_spans))
            blocks.extend(hoisted)
        elif kind == "Image":
            ref = refs.get(id(child), "")
            alt = child.get("alt") or ""
            blocks.append(m.image(resources.get(ref), alt=normalize_text(alt), reference=ref))
        elif kind == "InsertedFile":
            ref = refs.get(id(child), "")
            name = child.get("preferredName") or ""
            blocks.append(m.attachment(resources.get(ref), name=name, reference=ref))
        elif kind == "Table":
            blocks.append(_parse_table(child, refs, resources, warnings))
        elif kind in _UNSUPPORTED_OBJECTS:
            blocks.append(m.placeholder(_UNSUPPORTED_OBJECTS[kind]))
        elif kind == "OEChildren":
            blocks.extend(_parse_oes(list(child), refs, resources, warnings))
        elif kind in {"List", "Tag", "MediaIndex", "Meta"}:
            continue  # structural/metadata elements handled elsewhere
        elif kind:
            warnings.append(f"unsupported OneNote element: {kind}")
            blocks.append(m.unsupported(kind))
    return blocks


def _parse_table(table_el, refs, resources, warnings) -> m.Node:
    rows: list[list[list[m.Node]]] = []
    for row_el in list(table_el):
        if _local(row_el.tag) != "Row":
            continue
        cells: list[list[m.Node]] = []
        for cell_el in list(row_el):
            if _local(cell_el.tag) != "Cell":
                continue
            cells.append(_parse_oe_children(cell_el, refs, resources, warnings))
        rows.append(cells)
    return m.table(rows)


def _list_marker(oe) -> tuple[bool, bool]:
    for child in list(oe):
        if _local(child.tag) == "List":
            for marker in list(child):
                if _local(marker.tag) == "Number":
                    return True, True
            return True, False
    return False, False


def _checklist_state(oe) -> bool | None:
    for child in list(oe):
        if _local(child.tag) == "Tag":
            completed = child.get("completed")
            if completed is not None:
                return completed == "true"
    return None
