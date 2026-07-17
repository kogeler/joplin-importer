"""Audit orchestration: snapshots in, matches + findings out.

Records scanned without (or with an outdated) normalizer are re-normalized
here from the raw content stored in the snapshot, so a normalizer upgrade
never requires a rescan of live systems.
"""

from __future__ import annotations

import json
from collections import Counter

from ..models import ContentFormat, NoteRecord, PageRecord, SnapshotReader
from ..models.hashing import sha256_canonical_json
from ..normalization import Normalizer
from ..normalization.model import Node, collect_links
from .detection import detect_findings
from .engine import match_records
from .results import AuditResult, AuditSummary
from .scoring import MatchingConfig


class AuditContext:
    """Loads snapshot records, guaranteeing up-to-date normalization."""

    def __init__(self, source: SnapshotReader, target: SnapshotReader) -> None:
        self.source = source
        self.target = target
        self._normalizer = Normalizer()
        self._page_models: dict[str, Node] = {}
        self._note_models: dict[str, Node] = {}
        self.pages: list[PageRecord] = [
            self._ensure_page(p) for p in source.iter_pages()
        ]
        self.notes: list[NoteRecord] = [
            self._ensure_note(n) for n in target.iter_notes()
        ]

    # -- model loaders for detection ------------------------------------------

    def page_model(self, page_id: str) -> Node | None:
        return self._page_models.get(page_id) or self._load_stored(
            self.source, self._by_page_id().get(page_id)
        )

    def note_model(self, note_id: str) -> Node | None:
        return self._note_models.get(note_id) or self._load_stored(
            self.target, self._by_note_id().get(note_id)
        )

    # -- internals ---------------------------------------------------------------

    def _by_page_id(self) -> dict[str, PageRecord]:
        return {p.source_page_id: p for p in self.pages}

    def _by_note_id(self) -> dict[str, NoteRecord]:
        return {n.joplin_note_id: n for n in self.notes}

    def _ensure_page(self, page: PageRecord) -> PageRecord:
        if page.normalizer_version == self._normalizer.version:
            return page
        raw = self._read_raw(self.source, page)
        if raw is None:
            return page
        normalized = self._normalizer.normalize(
            ContentFormat(page.raw_content_format), raw, _resource_map(page)
        )
        self._page_models[page.source_page_id] = normalized.semantic_model
        return _apply(page, normalized)

    def _ensure_note(self, note: NoteRecord) -> NoteRecord:
        if note.normalizer_version == self._normalizer.version:
            return note
        raw = self._read_raw(self.target, note)
        if raw is None:
            return note
        normalized = self._normalizer.normalize(
            note.content_format, raw, _resource_map(note)
        )
        self._note_models[note.joplin_note_id] = normalized.semantic_model
        return _apply(note, normalized)

    @staticmethod
    def _read_raw(reader: SnapshotReader, record) -> str | None:
        if not record.raw_content_path:
            return None
        try:
            return reader.read_relative(record.raw_content_path).decode("utf-8")
        except (OSError, UnicodeDecodeError, Exception):
            return None

    @staticmethod
    def _load_stored(reader: SnapshotReader, record) -> Node | None:
        if record is None or not record.semantic_model_path:
            return None
        try:
            return json.loads(reader.read_relative(record.semantic_model_path))
        except Exception:
            return None


def _resource_map(record) -> dict[str, str]:
    return {r.source_reference: r.sha256 for r in record.resources if r.sha256}


def _apply(record, normalized):
    record.normalizer_version = normalized.version
    record.normalized_text = normalized.normalized_text
    record.normalized_text_sha256 = normalized.normalized_text_sha256
    record.semantic_model_sha256 = normalized.semantic_sha256
    record.visible_text_length = len(normalized.normalized_text)
    record.link_targets = collect_links(normalized.semantic_model)
    return record


def run_audit(
    source: SnapshotReader,
    target: SnapshotReader,
    *,
    tool_version: str,
    config: MatchingConfig | None = None,
) -> AuditResult:
    config = config or MatchingConfig()
    context = AuditContext(source, target)

    matches = match_records(context.pages, context.notes, config)
    findings = detect_findings(
        context.pages,
        context.notes,
        matches,
        load_page_model=context.page_model,
        load_note_model=context.note_model,
        source_coverage_complete=(
            source.manifest.coverage_status == "complete"
            and target.manifest.coverage_status == "complete"
        ),
    )

    summary = AuditSummary(
        tool_version=tool_version,
        threshold_version=config.version,
        source_snapshot_id=source.manifest.snapshot_id,
        target_snapshot_id=target.manifest.snapshot_id,
        source_manifest_hash=sha256_canonical_json(source.manifest.canonical_dict()),
        target_manifest_hash=sha256_canonical_json(target.manifest.canonical_dict()),
        source_pages=len(context.pages),
        target_notes=len(context.notes),
        matches_by_confidence=dict(
            Counter(str(m.confidence) for m in matches if m.source_page_id)
        ),
        findings_by_kind=dict(Counter(str(f.kind) for f in findings)),
        findings_by_evidence=dict(Counter(str(f.evidence) for f in findings)),
        findings_by_cause=dict(Counter(str(f.cause) for f in findings)),
    )
    return AuditResult(summary=summary, matches=matches, findings=findings)
