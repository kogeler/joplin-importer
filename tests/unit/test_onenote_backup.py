"""Latest-only discovery and read-only OneNote backup extraction."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from joplin_importer.adapters.onenote_backup import scanner as backup_scanner
from joplin_importer.adapters.onenote_backup.discovery import (
    BackupDiscoveryError,
    discover_backup_root,
    discover_latest_sections,
)
from joplin_importer.adapters.onenote_backup.scanner import scan_onenote_backup
from joplin_importer.models import (
    AuditRole,
    SnapshotReader,
    SnapshotWriter,
    SourceBackend,
    sha256_file,
)
from joplin_importer.normalization import Normalizer
from tests.fake_onenote_backup import (
    AttachedFile,
    Document,
    Image,
    Outline,
    OutlineElement,
    Page,
    RichText,
    Table,
    TableCell,
    TableRow,
    TextRun,
)


def _write_one(path: Path, data: bytes, mtime_ns: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, ns=(mtime_ns, mtime_ns))
    return path


def test_auto_discovery_does_not_depend_on_localized_directory_name(tmp_path):
    local = tmp_path / "local"
    expected = local / "Microsoft" / "OneNote" / "16.0" / "arbitrary-localized-name"
    _write_one(expected / "Notebook" / "Section ( Backup 2026-07-17).one", b"one", 1)
    _write_one(
        local / "Microsoft" / "OneNote" / "15.0" / "other" / "Book" / "Old.one",
        b"old",
        1,
    )

    actual = discover_backup_root(env={"LOCALAPPDATA": str(local)})

    assert actual == expected.resolve()


def test_manual_root_bypasses_auto_discovery(tmp_path):
    manual = tmp_path / "manual"
    _write_one(manual / "Book" / "Section.one", b"one", 1)

    assert discover_backup_root(manual, env={}) == manual.resolve()


def test_auto_discovery_failure_requests_manual_root(tmp_path):
    with pytest.raises(BackupDiscoveryError, match="--backup-root"):
        discover_backup_root(env={"LOCALAPPDATA": str(tmp_path)})


def test_latest_section_selected_and_internal_parentheses_preserved(tmp_path):
    root = tmp_path / "backup"
    older = _write_one(
        root / "Book" / "Research (topic) ( Backup 2026-07-16).one", b"older", 10
    )
    newest = _write_one(
        root / "Book" / "Research (topic) ( Backup 2026-07-17 - 2).one", b"newest", 20
    )

    inventory = discover_latest_sections(root)

    assert inventory.physical_file_count == 2
    assert inventory.logical_section_count == 1
    assert inventory.older_versions_skipped == 1
    assert inventory.sections[0].path == newest
    assert inventory.sections[0].path != older
    assert inventory.sections[0].section_title == "Research (topic)"


def test_recycle_bin_is_excluded_by_default(tmp_path):
    root = tmp_path / "backup"
    _write_one(root / "Book" / "Normal ( Backup 2026-07-17).one", b"normal", 1)
    _write_one(
        root / "Book" / "OneNote_RecycleBin" / "Deleted ( Backup 2026-07-17).one",
        b"deleted",
        1,
    )

    excluded = discover_latest_sections(root)
    included = discover_latest_sections(root, include_recycle_bin=True)

    assert excluded.logical_section_count == 1
    assert excluded.recycle_bin_files_skipped == 1
    assert included.logical_section_count == 2


def test_scanner_reads_only_latest_and_captures_content_resources(tmp_path):
    root = tmp_path / "backup"
    older = _write_one(root / "Book" / "Section ( Backup 2026-07-16).one", b"old", 10)
    newest = _write_one(root / "Book" / "Section ( Backup 2026-07-17).one", b"new", 20)
    source_hashes = {path: sha256_file(path) for path in (older, newest)}
    loaded: list[Path] = []
    field_link = RichText()
    field_link.Text = (
        '\ufddfHYPERLINK "https://field.example.invalid/path?a=1&b=2"Field label tail'
    )
    field_link.TextRuns = [
        TextRun('\ufddfHYPERLINK "https://field.example.invalid/path?a=1&b=2"'),
        TextRun("Field label"),
        TextRun(" tail"),
    ]
    page = Page(
        "Recovered page",
        Outline(
            OutlineElement(RichText("Visible text", hyperlink="https://example.invalid")),
            OutlineElement(field_link),
            OutlineElement(
                RichText("First item"), number_list=SimpleNamespace(NumberFormat="1")
            ),
            Image(),
            AttachedFile(),
            Table(TableRow(TableCell(RichText("Cell text")))),
        ),
    )

    def loader(path: Path):
        loaded.append(path)
        return Document(page)

    output = tmp_path / "snapshot"
    manifest = scan_onenote_backup(
        root,
        SnapshotWriter(output),
        tool_version="test",
        snapshot_id="backup-test",
        normalizer=Normalizer(),
        document_loader=loader,
    )

    assert loaded == [newest]
    assert {path: sha256_file(path) for path in (older, newest)} == source_hashes
    assert manifest.source_backend == SourceBackend.ONENOTE_BACKUP
    assert manifest.audit_role == AuditRole.CORROBORATING
    assert manifest.configuration["backup_selection"] == "latest"
    assert manifest.record_counts["older_versions_skipped"] == 1
    assert manifest.record_counts["resources"] == 2
    reader = SnapshotReader(output)
    record = next(reader.iter_pages())
    assert record.page_title == "Recovered page"
    assert record.normalized_text == "Visible text\nField label tail\nFirst item\nCell text"
    assert record.image_count == 1
    assert record.attachment_count == 1
    assert len(record.resource_hashes) == 2
    assert record.link_targets == [
        "https://example.invalid",
        "https://field.example.invalid/path?a=1&b=2",
    ]
    raw_html = (output / record.raw_content_path).read_text(encoding="utf-8")
    assert "\ufddfHYPERLINK" not in raw_html
    assert (
        '<a href="https://field.example.invalid/path?a=1&amp;b=2">Field label</a> tail'
        in raw_html
    )
    assert record.created_at == "2020-01-02T03:04:05Z"
    assert record.updated_at == "2021-02-03T04:05:06Z"
    assert str(root.resolve()) not in (output / "manifest.json").read_text(encoding="utf-8")


def test_scanner_isolates_section_failure_and_uses_relative_path(tmp_path):
    root = tmp_path / "backup"
    good = _write_one(root / "Book" / "Good ( Backup 2026-07-17).one", b"good", 1)
    bad = _write_one(root / "Book" / "Bad ( Backup 2026-07-17).one", b"bad", 1)

    def loader(path: Path):
        if path == bad:
            raise ValueError(f"cannot parse {root}")
        assert path == good
        return Document(Page("Good page", Outline(RichText("body"))))

    output = tmp_path / "snapshot"
    manifest = scan_onenote_backup(
        root,
        SnapshotWriter(output),
        tool_version="test",
        snapshot_id="backup-failure-test",
        normalizer=Normalizer(),
        document_loader=loader,
    )

    assert manifest.coverage_status == "partial"
    assert manifest.record_counts["pages"] == 1
    reader = SnapshotReader(output)
    error = next(reader.iter_errors())
    assert error.item_title == "Bad"
    assert "Book/Bad" in error.message
    assert str(root.resolve()) not in error.message


def test_version_proxy_parser_patch_is_in_memory_and_restored():
    property_id = backup_scanner._VERSION_PROXY_CONTEXT_PROPERTY
    fake_parser = SimpleNamespace(
        PROPERTY_TYPE_OVERRIDES_RAW={
            property_id: backup_scanner._ASPOSE_WRONG_NESTED_PROPERTY_TYPE
        }
    )

    with backup_scanner._version_proxy_parser_patch(fake_parser) as applied:
        assert applied is True
        assert (
            fake_parser.PROPERTY_TYPE_OVERRIDES_RAW[property_id]
            == backup_scanner._OPAQUE_FOUR_BYTE_COUNT_TYPE
        )

    assert (
        fake_parser.PROPERTY_TYPE_OVERRIDES_RAW[property_id]
        == backup_scanner._ASPOSE_WRONG_NESTED_PROPERTY_TYPE
    )
