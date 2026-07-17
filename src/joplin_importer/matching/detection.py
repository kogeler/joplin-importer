"""Detection rules.

Every finding carries an evidence class (confirmed/probable/uncertain/
informational) and a cause class (migration-loss/source-drift/
format-conversion/extractor-failure/target-extra/unknown). Because the exact
migration artifact is out of scope, drift vs loss is decided heuristically
from the estimated import window and always explained.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass

from ..models import (
    CauseClass,
    EvidenceClass,
    FindingKind,
    MatchConfidence,
    NoteRecord,
    PageRecord,
    ResourceStatus,
)
from ..models.timeutil import epoch_seconds
from ..normalization.model import Node
from . import features as f
from .results import Finding, MatchResult
from .semantic_diff import describe_diff, diff_models

_DAY_S = 86400.0
_TRUNCATION_MIN_MISSING = 100  # chars
_TRUNCATION_MIN_RATIO = 0.3
_TIMESTAMP_TOLERANCE_S = _DAY_S
_CONTAINMENT_THRESHOLD = 0.9

#: loads a record's semantic model, or None when unavailable
ModelLoader = Callable[[str], Node | None]


@dataclass
class ImportWindow:
    """Estimated time interval of the original Joplin import."""

    start_s: float | None
    end_s: float | None
    explanation: str

    def page_postdates_import(self, iso_utc: str | None) -> bool:
        ts = epoch_seconds(iso_utc)
        if ts is None or self.end_s is None:
            return False
        return ts > self.end_s + _DAY_S


def estimate_import_window(notes: list[NoteRecord]) -> ImportWindow:
    """The importer created all notes in one run: cluster note created_time."""
    stamps = sorted(
        ts for note in notes if (ts := epoch_seconds(note.created_at)) is not None
    )
    if not stamps:
        return ImportWindow(None, None, "no note creation timestamps available")
    median = statistics.median(stamps)
    cluster = [ts for ts in stamps if abs(ts - median) <= 7 * _DAY_S]
    coverage = len(cluster) / len(stamps)
    if coverage < 0.5:
        return ImportWindow(
            None,
            None,
            f"note creation times are spread out (cluster covers {coverage:.0%}); "
            "cannot estimate the import window",
        )
    return ImportWindow(
        min(cluster),
        max(cluster),
        f"import window estimated from {len(cluster)}/{len(stamps)} note creation times",
    )


def detect_findings(
    pages: list[PageRecord],
    notes: list[NoteRecord],
    matches: list[MatchResult],
    *,
    load_page_model: ModelLoader,
    load_note_model: ModelLoader,
    source_coverage_complete: bool = True,
) -> list[Finding]:
    findings: list[Finding] = []
    pages_by_id = {p.source_page_id: p for p in pages}
    notes_by_id = {n.joplin_note_id: n for n in notes}
    window = estimate_import_window(notes)

    matched_pairs: list[tuple[PageRecord, NoteRecord, MatchResult]] = []
    unmatched_pages: list[PageRecord] = []
    for match in matches:
        if match.source_page_id and match.joplin_note_id:
            page = pages_by_id.get(match.source_page_id)
            note = notes_by_id.get(match.joplin_note_id)
            if page and note and match.confidence != MatchConfidence.UNMATCHED:
                matched_pairs.append((page, note, match))
                continue
        if match.source_page_id and not match.joplin_note_id:
            page = pages_by_id.get(match.source_page_id)
            if page:
                unmatched_pages.append(page)
        elif match.joplin_note_id and not match.source_page_id:
            note = notes_by_id.get(match.joplin_note_id)
            if note:
                findings.append(_target_extra(note))

    for page in unmatched_pages:
        findings.append(_source_missing(page, window, source_coverage_complete))
        findings.extend(_collapsed_source(page, matched_pairs))

    for page, note, match in matched_pairs:
        findings.extend(
            _pair_findings(page, note, match, window, load_page_model, load_note_model)
        )

    findings.extend(_duplicate_targets(notes))
    return findings


# -- unmatched -------------------------------------------------------------------


def _source_missing(
    page: PageRecord, window: ImportWindow, coverage_complete: bool
) -> Finding:
    drift = window.page_postdates_import(page.created_at)
    if drift:
        cause = CauseClass.SOURCE_DRIFT
        evidence = EvidenceClass.INFORMATIONAL
        explanation = (
            "page appears to be created after the estimated import window, so it was "
            f"probably never part of the migration ({window.explanation})"
        )
    else:
        cause = CauseClass.MIGRATION_LOSS
        evidence = EvidenceClass.PROBABLE if coverage_complete else EvidenceClass.UNCERTAIN
        explanation = "no Joplin note matches this OneNote page"
        if not coverage_complete:
            explanation += "; source or target scan was incomplete, treat with caution"
    return Finding(
        kind=FindingKind.SOURCE_PAGE_MISSING,
        evidence=evidence,
        cause=cause,
        source_page_id=page.source_page_id,
        title=page.page_title,
        path=list(f.source_path(page)),
        explanation=explanation,
        details={"visible_text_length": page.visible_text_length},
    )


def _target_extra(note: NoteRecord) -> Finding:
    return Finding(
        kind=FindingKind.TARGET_NOTE_UNMATCHED,
        evidence=EvidenceClass.INFORMATIONAL,
        cause=CauseClass.TARGET_EXTRA,
        joplin_note_id=note.joplin_note_id,
        title=note.title,
        path=list(note.notebook_path),
        explanation="no OneNote page matches this Joplin note; it may be user-created",
        details={"status": str(note.status)},
    )


def _collapsed_source(
    page: PageRecord, matched_pairs: list[tuple[PageRecord, NoteRecord, MatchResult]]
) -> list[Finding]:
    """An unmatched page whose text is contained in another page's note suggests
    several OneNote pages collapsed into one Joplin note."""
    text = page.normalized_text
    if len(text) < 50:
        return []
    for other_page, note, _match in matched_pairs:
        if not note.normalized_text or len(note.normalized_text) <= len(text):
            continue
        if text in note.normalized_text:
            return [
                Finding(
                    kind=FindingKind.COLLAPSED_SOURCES,
                    evidence=EvidenceClass.PROBABLE,
                    cause=CauseClass.MIGRATION_LOSS,
                    source_page_id=page.source_page_id,
                    joplin_note_id=note.joplin_note_id,
                    title=page.page_title,
                    path=list(f.source_path(page)),
                    explanation=(
                        "this page's text appears verbatim inside the note matched to "
                        f"page {other_page.source_page_id!r} ({other_page.page_title!r})"
                    ),
                    details={"container_note_title": note.title},
                )
            ]
    return []


def _duplicate_targets(notes: list[NoteRecord]) -> list[Finding]:
    by_embedded: dict[str, list[NoteRecord]] = {}
    for note in notes:
        if note.embedded_onenote_page_id:
            by_embedded.setdefault(note.embedded_onenote_page_id, []).append(note)
    findings = []
    for page_id, group in sorted(by_embedded.items()):
        if len(group) > 1:
            findings.append(
                Finding(
                    kind=FindingKind.DUPLICATE_TARGETS,
                    evidence=EvidenceClass.CONFIRMED,
                    cause=CauseClass.MIGRATION_LOSS,
                    source_page_id=page_id,
                    joplin_note_id=group[0].joplin_note_id,
                    title=group[0].title,
                    path=list(group[0].notebook_path),
                    explanation=(
                        f"{len(group)} Joplin notes reference the same OneNote page ID"
                    ),
                    details={"note_ids": ", ".join(n.joplin_note_id for n in group)},
                )
            )
    return findings


# -- matched pairs -------------------------------------------------------------


def _pair_findings(
    page: PageRecord,
    note: NoteRecord,
    match: MatchResult,
    window: ImportWindow,
    load_page_model: ModelLoader,
    load_note_model: ModelLoader,
) -> list[Finding]:
    from typing import Any

    findings: list[Finding] = []
    drift = window.page_postdates_import(page.updated_at)
    base: dict[str, Any] = {
        "source_page_id": page.source_page_id,
        "joplin_note_id": note.joplin_note_id,
        "title": page.page_title,
        "path": list(f.source_path(page)),
    }

    def content_cause(default: CauseClass) -> CauseClass:
        return CauseClass.SOURCE_DRIFT if drift else default

    def soften(evidence: EvidenceClass) -> EvidenceClass:
        if drift and evidence in (EvidenceClass.CONFIRMED, EvidenceClass.PROBABLE):
            return EvidenceClass.UNCERTAIN
        return evidence

    if drift:
        findings.append(
            Finding(
                kind=FindingKind.SOURCE_DRIFT,
                evidence=EvidenceClass.INFORMATIONAL,
                cause=CauseClass.SOURCE_DRIFT,
                explanation=(
                    "the OneNote page was modified after the estimated import window; "
                    "differences below may be later edits rather than migration loss "
                    f"({window.explanation})"
                ),
                **base,
            )
        )

    # empty / truncated bodies -------------------------------------------------
    src_len = page.visible_text_length
    dst_len = note.visible_text_length
    src_media = page.image_count + page.attachment_count
    dst_media = note.image_count + note.attachment_count

    if src_len > 0 and dst_len == 0:
        findings.append(
            Finding(
                kind=FindingKind.EMPTY_BODY,
                evidence=soften(EvidenceClass.CONFIRMED),
                cause=content_cause(CauseClass.MIGRATION_LOSS),
                explanation=(
                    f"source page has {src_len} chars of visible text but the "
                    "matched Joplin note body is empty"
                ),
                details={"source_text_length": src_len},
                **base,
            )
        )
    elif src_len == 0 and src_media > 0 and dst_len == 0 and dst_media == 0:
        findings.append(
            Finding(
                kind=FindingKind.EMPTY_BODY,
                evidence=soften(EvidenceClass.CONFIRMED),
                cause=content_cause(CauseClass.MIGRATION_LOSS),
                explanation=(
                    "source page contains only images/attachments "
                    f"({page.image_count} image(s), {page.attachment_count} attachment(s)) "
                    "but the matched Joplin note is completely empty"
                ),
                details={
                    "source_images": page.image_count,
                    "source_attachments": page.attachment_count,
                },
                **base,
            )
        )
    elif (
        dst_len < src_len
        and (src_len - dst_len) >= _TRUNCATION_MIN_MISSING
        and (src_len - dst_len) / max(src_len, 1) >= _TRUNCATION_MIN_RATIO
    ):
        findings.append(
            Finding(
                kind=FindingKind.TRUNCATED_TEXT,
                evidence=soften(EvidenceClass.PROBABLE),
                cause=content_cause(CauseClass.MIGRATION_LOSS),
                explanation=(
                    f"source has {src_len} chars of visible text, note has {dst_len}; "
                    f"{src_len - dst_len} chars are missing"
                ),
                details={"source_text_length": src_len, "target_text_length": dst_len},
                **base,
            )
        )

    # semantic comparison ----------------------------------------------------------
    findings.extend(
        _semantic_findings(
            page, note, base, soften, content_cause, load_page_model, load_note_model
        )
    )

    # resources ---------------------------------------------------------------------
    findings.extend(_resource_findings(page, note, base, soften, content_cause))

    # placement / hierarchy / metadata ---------------------------------------------
    if tuple(p.casefold() for p in f.source_path(page)) != tuple(
        p.casefold() for p in f.target_path(note)
    ):
        findings.append(
            Finding(
                kind=FindingKind.WRONG_PLACEMENT,
                evidence=EvidenceClass.INFORMATIONAL,
                cause=CauseClass.MIGRATION_LOSS,
                explanation=(
                    f"source path {list(f.source_path(page))!r} differs from "
                    f"note path {list(f.target_path(note))!r}"
                ),
                **base,
            )
        )

    if page.page_level > 1:
        findings.append(
            Finding(
                kind=FindingKind.LOST_HIERARCHY,
                evidence=EvidenceClass.INFORMATIONAL,
                cause=CauseClass.FORMAT_CONVERSION,
                explanation=(
                    f"source is a level-{page.page_level} subpage; Joplin has no "
                    "page hierarchy inside a notebook"
                ),
                **base,
            )
        )

    created_delta = _abs_delta(page.created_at, note.user_created_at)
    if created_delta is not None and created_delta > _TIMESTAMP_TOLERANCE_S:
        findings.append(
            Finding(
                kind=FindingKind.LOST_TIMESTAMPS,
                evidence=EvidenceClass.CONFIRMED,
                cause=CauseClass.MIGRATION_LOSS,
                explanation=(
                    "note user_created_time differs from the OneNote creation time by "
                    f"{created_delta / _DAY_S:.1f} day(s)"
                ),
                details={"source_created": page.created_at, "target_created": note.user_created_at},
                **base,
            )
        )

    if f.is_placeholder_title(note.title) and not f.is_placeholder_title(page.page_title):
        findings.append(
            Finding(
                kind=FindingKind.PLACEHOLDER_TITLE,
                evidence=EvidenceClass.CONFIRMED,
                cause=CauseClass.MIGRATION_LOSS,
                explanation=(
                    f"note title {note.title!r} is an import placeholder; source title "
                    f"is {page.page_title!r}"
                ),
                **base,
            )
        )

    if any("unsupported OneNote object" in w for w in page.warnings):
        objects = [w for w in page.warnings if "unsupported OneNote object" in w]
        findings.append(
            Finding(
                kind=FindingKind.UNSUPPORTED_CONTENT,
                evidence=EvidenceClass.INFORMATIONAL,
                cause=CauseClass.FORMAT_CONVERSION,
                explanation="; ".join(objects),
                **base,
            )
        )
    return findings


def _semantic_findings(
    page: PageRecord,
    note: NoteRecord,
    base: dict,
    soften,
    content_cause,
    load_page_model: ModelLoader,
    load_note_model: ModelLoader,
) -> list[Finding]:
    if not page.semantic_model_sha256 or not note.semantic_model_sha256:
        return []
    if page.normalizer_version != note.normalizer_version:
        return []
    if page.semantic_model_sha256 == note.semantic_model_sha256:
        if page.raw_content_format != note.raw_content_format:
            return [
                Finding(
                    kind=FindingKind.REPRESENTATION_ONLY,
                    evidence=EvidenceClass.INFORMATIONAL,
                    cause=CauseClass.FORMAT_CONVERSION,
                    explanation=(
                        f"raw formats differ ({page.raw_content_format} vs "
                        f"{note.raw_content_format}) but the canonical semantic models "
                        "are identical; not data loss"
                    ),
                    **base,
                )
            ]
        return []

    source_model = load_page_model(page.source_page_id)
    target_model = load_note_model(note.joplin_note_id)
    details: dict = {}
    explanation = "canonical semantic models differ"
    diff = None
    if source_model is not None and target_model is not None:
        diff = diff_models(source_model, target_model)
        explanation = describe_diff(diff)
        details = {
            "missing_blocks": diff["missing_blocks"],
            "extra_blocks": diff["extra_blocks"],
            "missing_by_kind": str(diff["missing_by_kind"]),
            "extra_by_kind": str(diff["extra_by_kind"]),
        }

    same_text = page.normalized_text == note.normalized_text
    if same_text:
        return [
            Finding(
                kind=FindingKind.FORMAT_CONVERSION_LOSS,
                evidence=EvidenceClass.PROBABLE,
                cause=CauseClass.FORMAT_CONVERSION,
                explanation=(
                    "visible text is identical but structure differs "
                    "(likely lost during HTML-to-Markdown conversion): " + explanation
                ),
                details=details,
                **base,
            )
        ]
    return [
        Finding(
            kind=FindingKind.SEMANTIC_DIFFERENCE,
            evidence=soften(EvidenceClass.PROBABLE),
            cause=content_cause(CauseClass.MIGRATION_LOSS),
            explanation=explanation,
            details=details,
            **base,
        )
    ]


def _resource_findings(
    page: PageRecord, note: NoteRecord, base: dict, soften, content_cause
) -> list[Finding]:
    findings: list[Finding] = []
    src_images = {r.sha256 for r in page.resources if r.is_image and r.sha256}
    dst_images = {r.sha256 for r in note.resources if r.is_image and r.sha256}
    src_files = {r.sha256 for r in page.resources if not r.is_image and r.sha256}
    dst_files = {r.sha256 for r in note.resources if not r.is_image and r.sha256}

    missing_images = src_images - dst_images
    if missing_images:
        if page.image_count > note.image_count:
            findings.append(
                Finding(
                    kind=FindingKind.MISSING_IMAGES,
                    evidence=soften(EvidenceClass.CONFIRMED),
                    cause=content_cause(CauseClass.MIGRATION_LOSS),
                    explanation=(
                        f"{page.image_count - note.image_count} image(s) missing "
                        f"({len(missing_images)} source image hash(es) not found in the note)"
                    ),
                    details={"source_images": page.image_count, "target_images": note.image_count},
                    **base,
                )
            )
        else:
            findings.append(
                Finding(
                    kind=FindingKind.RESOURCE_HASH_MISMATCH,
                    evidence=EvidenceClass.UNCERTAIN,
                    cause=CauseClass.FORMAT_CONVERSION,
                    explanation=(
                        "image counts match but content hashes differ; the importer may "
                        "have re-encoded images"
                    ),
                    **base,
                )
            )

    missing_files = src_files - dst_files
    if missing_files and page.attachment_count > note.attachment_count:
        findings.append(
            Finding(
                kind=FindingKind.MISSING_ATTACHMENTS,
                evidence=soften(EvidenceClass.CONFIRMED),
                cause=content_cause(CauseClass.MIGRATION_LOSS),
                explanation=(
                    f"{page.attachment_count - note.attachment_count} attachment(s) missing "
                    f"({len(missing_files)} source attachment hash(es) not found in the note)"
                ),
                details={
                    "source_attachments": page.attachment_count,
                    "target_attachments": note.attachment_count,
                },
                **base,
            )
        )
    elif missing_files:
        findings.append(
            Finding(
                kind=FindingKind.RESOURCE_HASH_MISMATCH,
                evidence=EvidenceClass.UNCERTAIN,
                cause=CauseClass.FORMAT_CONVERSION,
                explanation="attachment counts match but content hashes differ",
                **base,
            )
        )

    broken = [r for r in note.resources if r.status == ResourceStatus.MISSING]
    if broken:
        findings.append(
            Finding(
                kind=FindingKind.BROKEN_RESOURCE_REFERENCE,
                evidence=EvidenceClass.CONFIRMED,
                cause=CauseClass.UNKNOWN,
                explanation=(
                    f"{len(broken)} resource reference(s) in the note body point to "
                    "resources that are not attached or not downloadable"
                ),
                details={"references": ", ".join(r.source_reference for r in broken)},
                **base,
            )
        )
    return findings


def _abs_delta(a_iso: str | None, b_iso: str | None) -> float | None:
    a, b = epoch_seconds(a_iso), epoch_seconds(b_iso)
    if a is None or b is None:
        return None
    return abs(a - b)
