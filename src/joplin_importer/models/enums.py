"""Shared enumerations for records, findings, and repair actions."""

from __future__ import annotations

from enum import StrEnum


class SourceBackend(StrEnum):
    """Which extractor produced a snapshot."""

    ONENOTE_COM = "onenote-com"
    ONENOTE_GRAPH = "onenote-graph"
    ONENOTE_BACKUP = "onenote-backup"
    JOPLIN_API = "joplin-api"


class AuditRole(StrEnum):
    """Role of a snapshot in the audit."""

    AUTHORITATIVE_CURRENT = "authoritative-current"
    CORROBORATING = "corroborating"
    TARGET = "target"


class ContentFormat(StrEnum):
    """Detected/declared content format of a body."""

    ONENOTE_XML = "onenote-xml"
    HTML = "html"
    MARKDOWN = "markdown"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class MatchConfidence(StrEnum):
    """Confidence bucket for a source-to-target match."""

    EXACT = "exact"
    HIGH_CONFIDENCE = "high-confidence"
    PROBABLE = "probable"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


class EvidenceClass(StrEnum):
    """How strongly a finding is supported."""

    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    UNCERTAIN = "uncertain"
    INFORMATIONAL = "informational"


class CauseClass(StrEnum):
    """Most likely cause of a finding."""

    MIGRATION_LOSS = "migration-loss"
    SOURCE_DRIFT = "source-drift"
    FORMAT_CONVERSION = "format-conversion"
    EXTRACTOR_FAILURE = "extractor-failure"
    TARGET_EXTRA = "target-extra"
    UNKNOWN = "unknown"


class FindingKind(StrEnum):
    """Detection rule identifiers."""

    SOURCE_PAGE_MISSING = "source-page-missing"
    TARGET_NOTE_UNMATCHED = "target-note-unmatched"
    EMPTY_BODY = "empty-body"
    TRUNCATED_TEXT = "truncated-text"
    SEMANTIC_DIFFERENCE = "semantic-difference"
    MISSING_IMAGES = "missing-images"
    MISSING_ATTACHMENTS = "missing-attachments"
    RESOURCE_HASH_MISMATCH = "resource-hash-mismatch"
    BROKEN_RESOURCE_REFERENCE = "broken-resource-reference"
    EXTRACTOR_DISAGREEMENT = "extractor-disagreement"
    DUPLICATE_TARGETS = "duplicate-targets"
    COLLAPSED_SOURCES = "collapsed-sources"
    WRONG_PLACEMENT = "wrong-placement"
    LOST_HIERARCHY = "lost-hierarchy"
    LOST_TIMESTAMPS = "lost-timestamps"
    PLACEHOLDER_TITLE = "placeholder-title"
    UNSUPPORTED_CONTENT = "unsupported-content"
    REPRESENTATION_ONLY = "representation-only"
    FORMAT_CONVERSION_LOSS = "format-conversion-loss"
    SOURCE_DRIFT = "source-drift"


class RepairActionType(StrEnum):
    """Supported repair actions."""

    CREATE_RECOVERY_COPY = "create-recovery-copy"
    REPLACE_EMPTY_NOTE = "replace-empty-note"
    APPEND_RECOVERY_LINK = "append-recovery-link"
    MOVE_RECOVERED_NOTE = "move-recovered-note"
    ATTACH_MISSING_RESOURCE = "attach-missing-resource"


class NoteStatus(StrEnum):
    """Joplin note lifecycle status."""

    NORMAL = "normal"
    TRASHED = "trashed"
    CONFLICT = "conflict"


class ResourceStatus(StrEnum):
    """Outcome of extracting/downloading a resource."""

    OK = "ok"
    MISSING = "missing"
    UNREADABLE = "unreadable"
    SKIPPED = "skipped"
