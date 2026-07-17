"""Pairwise match features.

Every feature is in [0, 1] or ``None`` when not applicable to the pair (e.g.
text similarity when both bodies are empty). Placeholder titles contribute no
evidence: title alone must never pair duplicate ``Untitled Page`` items.
"""

from __future__ import annotations

import math
from difflib import SequenceMatcher

from ..models import NoteRecord, PageRecord
from ..models.timeutil import epoch_seconds
from ..normalization.textnorm import normalize_inline

#: import-generated fallback titles carrying no matching evidence
PLACEHOLDER_TITLES = frozenset(
    {"", "untitled page", "untitled note", "untitled", "new page", "без названия"}
)

_TEXT_SIMILARITY_CAP = 5000  # chars fed to SequenceMatcher
_DISTINCTIVE_MIN_LEN = 15


def normalized_title(title: str) -> str:
    return normalize_inline(title).casefold()


def is_placeholder_title(title: str) -> bool:
    return normalized_title(title) in PLACEHOLDER_TITLES


def source_path(page: PageRecord) -> tuple[str, ...]:
    return (page.notebook_title, *page.section_group_path, page.section_title)


def target_path(note: NoteRecord) -> tuple[str, ...]:
    return tuple(note.notebook_path)


def title_similarity(page: PageRecord, note: NoteRecord) -> float | None:
    a = normalized_title(page.page_title)
    b = normalized_title(note.title)
    if a in PLACEHOLDER_TITLES or b in PLACEHOLDER_TITLES:
        return None  # no evidence either way
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def path_similarity(page: PageRecord, note: NoteRecord) -> float:
    src = tuple(part.casefold() for part in source_path(page))
    dst = tuple(part.casefold() for part in target_path(note))
    if not dst:
        return 0.0
    if src == dst:
        return 1.0
    # imported folder structure may nest differently; compare tail components
    if src and dst and src[-1] == dst[-1]:
        return 0.8  # same section title
    if src and src[0] in dst:
        return 0.4  # same notebook somewhere in the path
    return 0.0


def time_similarity(
    a_iso: str | None, b_iso: str | None, *, tolerance_s: float = 120.0
) -> float | None:
    a = epoch_seconds(a_iso)
    b = epoch_seconds(b_iso)
    if a is None or b is None:
        return None
    delta = abs(a - b)
    if delta <= tolerance_s:
        return 1.0
    # exponential decay: ~0.5 at one week, ~0 beyond a few months
    return math.exp(-delta / (7 * 24 * 3600 / math.log(2)))


def text_similarity(page: PageRecord, note: NoteRecord) -> float | None:
    """Similarity that recognizes truncation: a note that is a clean prefix of
    the source still identifies the same page (content integrity is judged
    separately by the detection rules)."""
    a = page.normalized_text[:_TEXT_SIMILARITY_CAP]
    b = note.normalized_text[:_TEXT_SIMILARITY_CAP]
    if not a and not b:
        return None
    if not a or not b:
        return 0.0
    matcher = SequenceMatcher(None, a, b)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    ratio = 2.0 * matched / (len(a) + len(b))
    containment = matched / min(len(a), len(b))
    return max(ratio, containment * 0.95)


def resource_overlap(page: PageRecord, note: NoteRecord) -> float | None:
    a = set(page.resource_hashes)
    b = set(note.resource_hashes)
    if not a and not b:
        return None
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def count_similarity(page: PageRecord, note: NoteRecord) -> float | None:
    totals = [
        (page.image_count, note.image_count),
        (page.attachment_count, note.attachment_count),
    ]
    relevant = [(a, b) for a, b in totals if a or b]
    if not relevant:
        return None
    score = 0.0
    for a, b in relevant:
        score += min(a, b) / max(a, b)
    return score / len(relevant)


def url_overlap(page: PageRecord, note: NoteRecord) -> float | None:
    a = {u for u in page.link_targets if not u.startswith(":/")}
    b = {u for u in note.link_targets if not u.startswith(":/")}
    if not a and not b:
        return None
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def distinctive_fragment_overlap(page: PageRecord, note: NoteRecord) -> float | None:
    a = _distinctive_lines(page.normalized_text)
    b = _distinctive_lines(note.normalized_text)
    if not a and not b:
        return None
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    containment = len(a & b) / min(len(a), len(b))  # truncation-tolerant
    return max(jaccard, containment * 0.95)


def _distinctive_lines(text: str) -> set[str]:
    return {line for line in text.split("\n") if len(line) >= _DISTINCTIVE_MIN_LEN}


def order_similarity(page: PageRecord, note_index: int | None) -> float | None:
    """Placeholder: Joplin does not preserve page order; kept for completeness."""
    return None


def compute_features(page: PageRecord, note: NoteRecord) -> dict[str, float | None]:
    return {
        "title": title_similarity(page, note),
        "path": path_similarity(page, note),
        "created_time": time_similarity(page.created_at, note.user_created_at),
        "updated_time": time_similarity(page.updated_at, note.user_updated_at),
        "text": text_similarity(page, note),
        "semantic": _semantic_equality(page, note),
        "resources": resource_overlap(page, note),
        "counts": count_similarity(page, note),
        "urls": url_overlap(page, note),
        "fragments": distinctive_fragment_overlap(page, note),
        "level": 1.0 if page.page_level == 1 else 0.9,  # subpages slightly penalized
    }


def _semantic_equality(page: PageRecord, note: NoteRecord) -> float | None:
    if not page.semantic_model_sha256 or not note.semantic_model_sha256:
        return None
    if page.normalizer_version != note.normalizer_version:
        return None
    if not page.normalized_text and not note.normalized_text:
        return None  # two empty models being equal proves nothing
    return 1.0 if page.semantic_model_sha256 == note.semantic_model_sha256 else 0.0
