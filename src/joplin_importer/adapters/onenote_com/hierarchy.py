"""Parsing of the OneNote hierarchy XML (GetHierarchy output).

Pure Python + defusedxml: testable on any OS with fixture XML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from defusedxml import ElementTree as SafeET

from .api import ONENOTE_2013_NAMESPACE


@dataclass
class PageStub:
    """One page as listed in the hierarchy, before content download."""

    page_id: str
    title: str
    level: int
    order: int  # 0-based position within its section
    created_at: str | None
    updated_at: str | None
    notebook_id: str
    notebook_title: str
    section_id: str
    section_title: str
    section_group_path: list[str] = field(default_factory=list)
    in_recycle_bin: bool = False


@dataclass
class Hierarchy:
    namespace: str
    pages: list[PageStub] = field(default_factory=list)
    notebook_count: int = 0
    section_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    match = re.match(r"\{(.+)\}", tag)
    return match.group(1) if match else ""


def parse_hierarchy(xml_text: str) -> Hierarchy:
    root = SafeET.fromstring(xml_text)
    namespace = _namespace(root.tag)
    hierarchy = Hierarchy(namespace=namespace)
    if namespace != ONENOTE_2013_NAMESPACE:
        hierarchy.warnings.append(
            f"unexpected OneNote XML namespace {namespace!r}; expected 2013 schema"
        )

    notebooks = [el for el in root.iter() if _local(el.tag) == "Notebook"]
    # GetHierarchy может вернуть и одиночный Notebook как корень
    if not notebooks and _local(root.tag) == "Notebook":
        notebooks = [root]
    hierarchy.notebook_count = len(notebooks)

    for notebook in notebooks:
        nb_id = notebook.get("ID", "")
        nb_title = notebook.get("name", notebook.get("nickname", ""))
        _walk_container(
            hierarchy,
            notebook,
            notebook_id=nb_id,
            notebook_title=nb_title,
            group_path=[],
            in_recycle_bin=_is_recycle_bin(notebook),
        )
    return hierarchy


def _walk_container(
    hierarchy: Hierarchy,
    container,
    *,
    notebook_id: str,
    notebook_title: str,
    group_path: list[str],
    in_recycle_bin: bool,
) -> None:
    for child in list(container):
        kind = _local(child.tag)
        if kind == "SectionGroup":
            _walk_container(
                hierarchy,
                child,
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                group_path=[*group_path, child.get("name", "")],
                in_recycle_bin=in_recycle_bin or _is_recycle_bin(child),
            )
        elif kind == "Section":
            _collect_section_pages(
                hierarchy,
                child,
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                group_path=group_path,
                in_recycle_bin=in_recycle_bin or _is_recycle_bin(child),
            )


def _collect_section_pages(
    hierarchy: Hierarchy,
    section,
    *,
    notebook_id: str,
    notebook_title: str,
    group_path: list[str],
    in_recycle_bin: bool,
) -> None:
    hierarchy.section_count += 1
    section_id = section.get("ID", "")
    section_title = section.get("name", "")
    order = 0
    for child in list(section):
        if _local(child.tag) != "Page":
            continue
        page_id = child.get("ID", "")
        if not page_id:
            hierarchy.warnings.append(
                f"page without ID in section {section_title!r}; skipped from inventory"
            )
            continue
        try:
            level = int(child.get("pageLevel", "1"))
        except ValueError:
            level = 1
        hierarchy.pages.append(
            PageStub(
                page_id=page_id,
                title=child.get("name", ""),
                level=level,
                order=order,
                created_at=child.get("dateTime"),
                updated_at=child.get("lastModifiedTime"),
                notebook_id=notebook_id,
                notebook_title=notebook_title,
                section_id=section_id,
                section_title=section_title,
                section_group_path=list(group_path),
                in_recycle_bin=in_recycle_bin,
            )
        )
        order += 1


def _is_recycle_bin(element) -> bool:
    return element.get("isRecycleBin") == "true" or element.get("isInRecycleBin") == "true"
