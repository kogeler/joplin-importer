"""Stable references for binary objects inside OneNote page XML.

Images and inserted files get ``image:N`` / ``file:N`` identifiers assigned in
document order. Both the COM resource extractor and the semantic normalizer
use this module, so a record's ResourceRecord.source_reference always matches
the semantic model's image/attachment reference.
"""

from __future__ import annotations


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def assign_references(root) -> dict[int, str]:
    """Map ``id(element)`` -> reference for every Image/InsertedFile element."""
    refs: dict[int, str] = {}
    image_index = 0
    file_index = 0
    for element in root.iter():
        kind = _local(element.tag)
        if kind == "Image":
            image_index += 1
            refs[id(element)] = f"image:{image_index}"
        elif kind == "InsertedFile":
            file_index += 1
            refs[id(element)] = f"file:{file_index}"
    return refs
