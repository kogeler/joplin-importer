"""Data models, snapshot IO, and deterministic hashing."""

from .enums import (
    AuditRole,
    CauseClass,
    ContentFormat,
    EvidenceClass,
    FindingKind,
    MatchConfidence,
    NoteStatus,
    RepairActionType,
    ResourceStatus,
    SourceBackend,
)
from .hashing import (
    canonical_json,
    sha256_bytes,
    sha256_canonical_json,
    sha256_file,
    sha256_text,
)
from .records import (
    SNAPSHOT_SCHEMA_VERSION,
    ContentAnalysis,
    ErrorRecord,
    Manifest,
    NoteRecord,
    PageRecord,
    ResourceRecord,
    ScanMetadata,
)
from .snapshot import SnapshotError, SnapshotReader, SnapshotWriter

__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "AuditRole",
    "CauseClass",
    "ContentAnalysis",
    "ContentFormat",
    "ErrorRecord",
    "EvidenceClass",
    "FindingKind",
    "Manifest",
    "MatchConfidence",
    "NoteRecord",
    "NoteStatus",
    "PageRecord",
    "RepairActionType",
    "ResourceRecord",
    "ResourceStatus",
    "ScanMetadata",
    "SnapshotError",
    "SnapshotReader",
    "SnapshotWriter",
    "SourceBackend",
    "canonical_json",
    "sha256_bytes",
    "sha256_canonical_json",
    "sha256_file",
    "sha256_text",
]
