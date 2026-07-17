"""Format-aware content normalization.

Entry point: :class:`Normalizer`. Produces the canonical semantic model and
the normalized visible text for any supported raw format. Raw hashes stay
format-specific; only semantic hashes may be compared across formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models.enums import ContentFormat
from ..models.hashing import sha256_text
from . import model
from .html_parser import parse_html
from .markdown_parser import parse_markdown
from .model import Node, semantic_hash, visible_text
from .onenote_parser import parse_onenote_xml

#: bump when normalization output changes; stored in every record
NORMALIZER_VERSION = "joplin-importer-normalizer/4"


@dataclass
class NormalizedContent:
    semantic_model: Node
    normalized_text: str
    normalized_text_sha256: str
    semantic_sha256: str
    version: str = NORMALIZER_VERSION
    warnings: list[str] = field(default_factory=list)

    @property
    def visible_text_length(self) -> int:
        return len(self.normalized_text)


class Normalizer:
    """Dispatches raw content to the right parser and packages the result."""

    version = NORMALIZER_VERSION

    def normalize(
        self,
        content_format: ContentFormat,
        raw_text: str,
        resource_map: dict[str, str] | None = None,
    ) -> NormalizedContent:
        warnings: list[str] = []
        if content_format is ContentFormat.ONENOTE_XML:
            semantic, warnings = parse_onenote_xml(raw_text, resource_map)
        elif content_format is ContentFormat.HTML:
            semantic, warnings = parse_html(raw_text, resource_map)
        elif content_format in (ContentFormat.MARKDOWN, ContentFormat.MIXED):
            semantic, warnings = parse_markdown(raw_text, resource_map)
        else:
            semantic, warnings = parse_markdown(raw_text, resource_map)
            warnings = [*warnings, "unknown content format; parsed as markdown"]

        text = visible_text(semantic)
        return NormalizedContent(
            semantic_model=semantic,
            normalized_text=text,
            normalized_text_sha256=sha256_text(text),
            semantic_sha256=semantic_hash(semantic),
            warnings=warnings,
        )

    def __call__(
        self,
        content_format: ContentFormat,
        raw_text: str,
        resource_map: dict[str, str] | None = None,
    ) -> NormalizedContent:
        return self.normalize(content_format, raw_text, resource_map)


__all__ = [
    "NORMALIZER_VERSION",
    "NormalizedContent",
    "Normalizer",
    "model",
    "parse_html",
    "parse_markdown",
    "parse_onenote_xml",
]
