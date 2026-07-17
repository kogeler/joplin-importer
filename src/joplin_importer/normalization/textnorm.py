"""Visible-text normalization.

Rules: NFC, unified line endings, meaningless whitespace collapsed without
merging words, non-breaking spaces treated as regular spaces, zero-width
characters removed. Meaningful line breaks (``\\n``) are preserved.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿"), None)
_NBSP_RE = re.compile(r"[   ]")
_SPACES_RE = re.compile(r"[ \t\f\v]+")


def normalize_text(text: str) -> str:
    """Normalize a block of visible text; preserves intentional newlines."""
    if not text:
        return ""
    result = unicodedata.normalize("NFC", text)
    result = result.replace("\r\n", "\n").replace("\r", "\n")
    result = result.translate(_ZERO_WIDTH)
    result = _NBSP_RE.sub(" ", result)
    result = _SPACES_RE.sub(" ", result)
    # blank lines are serialization artifacts inside a block: drop them,
    # meaningful single line breaks survive
    lines = [line.strip() for line in result.split("\n")]
    return "\n".join(line for line in lines if line)


def normalize_inline(text: str) -> str:
    """Normalize inline text where newlines are not meaningful."""
    return normalize_text(text.replace("\n", " "))


def normalize_inline_runs(
    runs: list[tuple[str, str]],
) -> tuple[str, list[dict[str, object]]]:
    """Normalize inline text while retaining exact hyperlink ranges.

    Each input tuple is ``(text, href)``; an empty href means ordinary text.
    Whitespace is normalized across run boundaries so formatting or link tags
    cannot change the visible-text projection.
    """

    annotated: list[tuple[str, str]] = []
    for value, href in runs:
        prepared = unicodedata.normalize("NFC", value or "")
        prepared = prepared.replace("\r\n", "\n").replace("\r", "\n")
        prepared = prepared.translate(_ZERO_WIDTH)
        prepared = _NBSP_RE.sub(" ", prepared)
        for character in prepared:
            if character in " \t\f\v":
                character = " "
            if character == " " and annotated and annotated[-1][0] == " ":
                # A collapsed boundary space belongs to neither side of a
                # link. This keeps anchors from swallowing surrounding space.
                if annotated[-1][1] != href:
                    annotated[-1] = (" ", "")
                continue
            annotated.append((character, href))

    lines: list[list[tuple[str, str]]] = [[]]
    for character, href in annotated:
        if character == "\n":
            lines.append([])
        else:
            lines[-1].append((character, href))

    kept_lines: list[list[tuple[str, str]]] = []
    for line in lines:
        while line and line[0][0] == " ":
            line.pop(0)
        while line and line[-1][0] == " ":
            line.pop()
        if line:
            kept_lines.append(line)

    normalized: list[tuple[str, str]] = []
    for index, line in enumerate(kept_lines):
        if index:
            normalized.append(("\n", ""))
        normalized.extend(line)

    text = "".join(character for character, _href in normalized)
    spans: list[dict[str, object]] = []
    start = 0
    while start < len(normalized):
        href = normalized[start][1]
        end = start + 1
        while end < len(normalized) and normalized[end][1] == href:
            end += 1
        if href:
            spans.append({"start": start, "end": end, "href": href})
        start = end
    return text, spans
