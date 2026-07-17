"""Snapshot writing and reading.

A scan writes into ``<final>.staging``; :meth:`SnapshotWriter.finalize`
computes checksums, builds ``inventory.sqlite``, writes the manifest, and
atomically renames the staging directory to the final name. If a desktop sync
client persistently locks that directory, a verified copy publishes the
manifest last as the atomic commit marker. A finalized snapshot is immutable.
An interrupted scan can resume in staging via ``SnapshotWriter(..., resume=True)``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from .hashing import canonical_json, sha256_bytes, sha256_canonical_json, sha256_file, sha256_text
from .records import (
    SNAPSHOT_SCHEMA_VERSION,
    ErrorRecord,
    Manifest,
    NoteRecord,
    PageRecord,
    ScanMetadata,
)

PAGES_DIR = "pages"
RESOURCES_DIR = "resources"
ERRORS_FILE = "errors.jsonl"
MANIFEST_FILE = "manifest.json"
SCAN_METADATA_FILE = "scan-metadata.json"
INVENTORY_FILE = "inventory.sqlite"

_RECORD_SUFFIX = ".record.json"
_FINALIZE_RENAME_ATTEMPTS = 8
_FINALIZE_RETRY_BASE_SECONDS = 0.05


class SnapshotError(RuntimeError):
    pass


def _safe_stem(record_id: str) -> str:
    """Filesystem-safe, deterministic stem for a record ID."""
    return sha256_text(record_id)[:32]


def _replace_directory_with_retry(source: Path, target: Path) -> None:
    """Commit staging despite transient or persistent desktop-sync locks."""
    for attempt in range(_FINALIZE_RENAME_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 == _FINALIZE_RENAME_ATTEMPTS:
                break
            time.sleep(_FINALIZE_RETRY_BASE_SECONDS * (2**attempt))
    _copy_with_manifest_commit(source, target)


def _copy_with_manifest_commit(source: Path, target: Path) -> None:
    """Copy a snapshot with the manifest published last as its commit marker."""
    if target.exists():
        raise SnapshotError(f"snapshot destination appeared during finalization: {target}")
    manifest_path = source / MANIFEST_FILE
    if not manifest_path.exists():
        raise SnapshotError(f"staging snapshot has no manifest: {source}")
    manifest_bytes = manifest_path.read_bytes()
    manifest = Manifest.model_validate_json(manifest_bytes)

    target.mkdir(parents=True)
    try:
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            destination = target / rel
            if path.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif rel.as_posix() != MANIFEST_FILE:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)

        problems = [
            rel
            for rel, expected in manifest.file_checksums.items()
            if not (target / rel).is_file() or sha256_file(target / rel) != expected
        ]
        if problems:
            raise SnapshotError(
                "copied snapshot failed checksum verification: " + ", ".join(problems[:5])
            )

        manifest_tmp = target / (MANIFEST_FILE + ".tmp")
        manifest_tmp.write_bytes(manifest_bytes)
        os.replace(manifest_tmp, target / MANIFEST_FILE)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise

    # The committed target is now complete. A sync client may still hold a
    # staging file open, so cleanup is best-effort and never invalidates it.
    shutil.rmtree(source, ignore_errors=True)


class SnapshotWriter:
    """Writes one snapshot directory through a staging area."""

    def __init__(self, final_dir: Path, *, resume: bool = False) -> None:
        self.final_dir = final_dir
        self.staging_dir = final_dir.parent / (final_dir.name + ".staging")
        self._finalized = False
        if final_dir.exists():
            raise SnapshotError(f"snapshot already finalized: {final_dir}")
        if self.staging_dir.exists() and not resume:
            raise SnapshotError(
                f"staging directory exists: {self.staging_dir}; pass resume=True to continue"
            )
        (self.staging_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)
        (self.staging_dir / RESOURCES_DIR).mkdir(parents=True, exist_ok=True)

    # -- record writing ----------------------------------------------------

    def write_raw_content(self, record_id: str, data: bytes, extension: str) -> tuple[str, str]:
        """Store raw page/note content; returns (snapshot-relative path, sha256)."""
        rel = f"{PAGES_DIR}/{_safe_stem(record_id)}.raw{extension}"
        self._write_bytes(rel, data)
        return rel, sha256_bytes(data)

    def write_semantic_model(self, record_id: str, model_json: dict) -> tuple[str, str]:
        """Store the canonical semantic model; returns (relative path, sha256)."""
        text = canonical_json(model_json)
        rel = f"{PAGES_DIR}/{_safe_stem(record_id)}.semantic.json"
        self._write_bytes(rel, text.encode("utf-8"))
        return rel, sha256_text(text)

    def write_resource(self, data: bytes, extension: str = "") -> tuple[str, str]:
        """Store a resource by content hash; returns (relative path, sha256)."""
        digest = sha256_bytes(data)
        rel = f"{RESOURCES_DIR}/{digest}{extension}"
        target = self.staging_dir / rel
        if not target.exists():  # content-addressed: identical data may repeat
            self._write_bytes(rel, data)
        return rel, digest

    def write_record(self, record: PageRecord | NoteRecord) -> None:
        record_id = _record_id(record)
        rel = f"{PAGES_DIR}/{_safe_stem(record_id)}{_RECORD_SUFFIX}"
        self._write_bytes(rel, canonical_json(record.canonical_dict()).encode("utf-8"))

    def has_record(self, record_id: str) -> bool:
        """True if a record with this ID is already staged (for resume)."""
        return (self.staging_dir / PAGES_DIR / f"{_safe_stem(record_id)}{_RECORD_SUFFIX}").exists()

    def add_error(self, error: ErrorRecord) -> None:
        path = self.staging_dir / ERRORS_FILE
        with path.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(error.canonical_dict()) + "\n")

    # -- finalization ------------------------------------------------------

    def finalize(self, manifest: Manifest, scan_metadata: ScanMetadata) -> Path:
        if self._finalized:
            raise SnapshotError("snapshot already finalized")

        records = sorted(self._iter_record_dicts(), key=lambda r: canonical_json(r))
        manifest.inventory_hash = sha256_canonical_json(records)
        manifest.record_counts.setdefault("records", len(records))
        manifest.record_counts.setdefault("errors", self._count_errors())

        self._build_inventory(records)
        self._write_bytes(
            SCAN_METADATA_FILE, canonical_json(scan_metadata.canonical_dict()).encode("utf-8")
        )

        checksums: dict[str, str] = {}
        for path in sorted(self.staging_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self.staging_dir).as_posix()
                checksums[rel] = sha256_file(path)
        manifest.file_checksums = checksums

        self._write_bytes(MANIFEST_FILE, canonical_json(manifest.canonical_dict()).encode("utf-8"))
        _replace_directory_with_retry(self.staging_dir, self.final_dir)
        self._finalized = True
        return self.final_dir

    # -- internals ---------------------------------------------------------

    def _write_bytes(self, rel: str, data: bytes) -> None:
        target = self.staging_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)

    def _iter_record_dicts(self) -> Iterator[dict]:
        pages = self.staging_dir / PAGES_DIR
        for path in sorted(pages.glob(f"*{_RECORD_SUFFIX}")):
            yield json.loads(path.read_text(encoding="utf-8"))

    def _count_errors(self) -> int:
        path = self.staging_dir / ERRORS_FILE
        if not path.exists():
            return 0
        with path.open(encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def _build_inventory(self, records: list[dict]) -> None:
        db_path = self.staging_dir / INVENTORY_FILE
        if db_path.exists():
            db_path.unlink()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "CREATE TABLE records ("
                " record_id TEXT PRIMARY KEY,"
                " kind TEXT NOT NULL,"
                " title TEXT,"
                " json TEXT NOT NULL)"
            )
            for rec in records:
                conn.execute(
                    "INSERT INTO records (record_id, kind, title, json) VALUES (?, ?, ?, ?)",
                    (
                        rec.get("source_page_id") or rec.get("joplin_note_id") or "",
                        "page" if "source_page_id" in rec else "note",
                        rec.get("page_title") or rec.get("title") or "",
                        canonical_json(rec),
                    ),
                )
            conn.commit()
        finally:
            conn.close()


class SnapshotReader:
    """Reads a finalized snapshot."""

    def __init__(self, snapshot_dir: Path) -> None:
        self.snapshot_dir = snapshot_dir
        manifest_path = snapshot_dir / MANIFEST_FILE
        if not manifest_path.exists():
            raise SnapshotError(f"not a finalized snapshot (no manifest): {snapshot_dir}")
        self.manifest = Manifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.snapshot_schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotError(
                f"unsupported snapshot schema version {self.manifest.snapshot_schema_version}"
            )

    def scan_metadata(self) -> ScanMetadata:
        path = self.snapshot_dir / SCAN_METADATA_FILE
        return ScanMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    def iter_pages(self) -> Iterator[PageRecord]:
        for rec in self._iter_record_dicts():
            if "source_page_id" in rec:
                yield PageRecord.model_validate(rec)

    def iter_notes(self) -> Iterator[NoteRecord]:
        for rec in self._iter_record_dicts():
            if "joplin_note_id" in rec:
                yield NoteRecord.model_validate(rec)

    def iter_errors(self) -> Iterator[ErrorRecord]:
        path = self.snapshot_dir / ERRORS_FILE
        if not path.exists():
            return
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield ErrorRecord.model_validate_json(line)

    def read_relative(self, rel: str) -> bytes:
        """Read a snapshot file referenced by a record, safely.

        Rejects absolute paths and traversal outside the snapshot root even if
        a record was tampered with.
        """
        candidate = (self.snapshot_dir / rel).resolve()
        root = self.snapshot_dir.resolve()
        if not candidate.is_relative_to(root):
            raise SnapshotError(f"path escapes snapshot root: {rel}")
        return candidate.read_bytes()

    def verify_checksums(self) -> list[str]:
        """Return a list of mismatched/missing files (empty means intact)."""
        problems: list[str] = []
        for rel, expected in self.manifest.file_checksums.items():
            path = self.snapshot_dir / rel
            if not path.exists():
                problems.append(f"missing: {rel}")
            elif sha256_file(path) != expected:
                problems.append(f"checksum mismatch: {rel}")
        return problems

    def _iter_record_dicts(self) -> Iterator[dict]:
        pages = self.snapshot_dir / PAGES_DIR
        for path in sorted(pages.glob(f"*{_RECORD_SUFFIX}")):
            yield json.loads(path.read_text(encoding="utf-8"))


def _record_id(record: PageRecord | NoteRecord) -> str:
    if isinstance(record, PageRecord):
        return record.source_page_id
    return record.joplin_note_id
