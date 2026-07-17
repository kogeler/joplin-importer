import pytest

import joplin_importer.models.snapshot as snapshot_module
from joplin_importer.models import (
    AuditRole,
    ContentFormat,
    ErrorRecord,
    Manifest,
    NoteRecord,
    PageRecord,
    ScanMetadata,
    SnapshotError,
    SnapshotReader,
    SnapshotWriter,
    SourceBackend,
)


def make_page(page_id: str = "{page-1}", title: str = "Page 1") -> PageRecord:
    return PageRecord(
        source_backend=SourceBackend.ONENOTE_COM,
        audit_role=AuditRole.AUTHORITATIVE_CURRENT,
        source_page_id=page_id,
        page_title=title,
        raw_content_format=ContentFormat.ONENOTE_XML,
    )


def make_manifest() -> Manifest:
    return Manifest(
        snapshot_id="snap-1",
        tool_version="0.1.0",
        source_backend=SourceBackend.ONENOTE_COM,
        audit_role=AuditRole.AUTHORITATIVE_CURRENT,
    )


def make_metadata() -> ScanMetadata:
    return ScanMetadata(
        started_at_utc="2026-07-17T00:00:00Z",
        finished_at_utc="2026-07-17T00:01:00Z",
        host_os="linux",
        tool_version="0.1.0",
        adapter="onenote-com",
    )


def write_snapshot(tmp_path, pages=None):
    final = tmp_path / "snap"
    writer = SnapshotWriter(final)
    for page in pages or [make_page()]:
        rel, digest = writer.write_raw_content(page.source_page_id, b"<xml/>", ".xml")
        page.raw_content_path = rel
        page.raw_content_sha256 = digest
        writer.write_record(page)
    writer.finalize(make_manifest(), make_metadata())
    return final


def test_finalize_renames_staging(tmp_path):
    final = write_snapshot(tmp_path)
    assert final.exists()
    assert not (tmp_path / "snap.staging").exists()
    assert (final / "manifest.json").exists()
    assert (final / "inventory.sqlite").exists()


def test_finalize_retries_transient_directory_lock(tmp_path, monkeypatch):
    final = tmp_path / "snap"
    writer = SnapshotWriter(final)
    writer.write_record(make_page())
    real_replace = snapshot_module.os.replace
    rename_attempts = 0

    def flaky_replace(source, target):
        nonlocal rename_attempts
        if source == writer.staging_dir:
            rename_attempts += 1
            if rename_attempts < 3:
                raise PermissionError("transient desktop-sync lock")
        return real_replace(source, target)

    monkeypatch.setattr(snapshot_module.os, "replace", flaky_replace)
    monkeypatch.setattr(snapshot_module.time, "sleep", lambda _seconds: None)

    writer.finalize(make_manifest(), make_metadata())

    assert rename_attempts == 3
    assert final.exists()


def test_finalize_copies_with_manifest_last_when_directory_stays_locked(
    tmp_path, monkeypatch
):
    final = tmp_path / "snap"
    writer = SnapshotWriter(final)
    writer.write_record(make_page())
    real_replace = snapshot_module.os.replace

    def locked_directory_replace(source, target):
        if source == writer.staging_dir:
            raise PermissionError("persistent desktop-sync lock")
        return real_replace(source, target)

    monkeypatch.setattr(snapshot_module.os, "replace", locked_directory_replace)
    monkeypatch.setattr(snapshot_module.time, "sleep", lambda _seconds: None)

    writer.finalize(make_manifest(), make_metadata())

    reader = SnapshotReader(final)
    assert reader.verify_checksums() == []
    assert len(list(reader.iter_pages())) == 1


def test_reader_roundtrip(tmp_path):
    final = write_snapshot(tmp_path)
    reader = SnapshotReader(final)
    pages = list(reader.iter_pages())
    assert len(pages) == 1
    assert pages[0].source_page_id == "{page-1}"
    assert reader.read_relative(pages[0].raw_content_path) == b"<xml/>"
    assert reader.verify_checksums() == []


def test_inventory_hash_is_deterministic(tmp_path):
    a = SnapshotReader(write_snapshot(tmp_path / "a")).manifest.inventory_hash
    b = SnapshotReader(write_snapshot(tmp_path / "b")).manifest.inventory_hash
    assert a == b


def test_inventory_hash_changes_with_content(tmp_path):
    a = SnapshotReader(write_snapshot(tmp_path / "a")).manifest.inventory_hash
    b = SnapshotReader(
        write_snapshot(tmp_path / "b", [make_page(title="Other")])
    ).manifest.inventory_hash
    assert a != b


def test_cannot_overwrite_finalized_snapshot(tmp_path):
    final = write_snapshot(tmp_path)
    with pytest.raises(SnapshotError):
        SnapshotWriter(final)


def test_resume_requires_flag(tmp_path):
    final = tmp_path / "snap"
    writer = SnapshotWriter(final)
    writer.write_record(make_page())
    with pytest.raises(SnapshotError):
        SnapshotWriter(final)
    resumed = SnapshotWriter(final, resume=True)
    assert resumed.has_record("{page-1}")
    assert not resumed.has_record("{page-2}")
    resumed.write_record(make_page("{page-2}", "Page 2"))
    resumed.finalize(make_manifest(), make_metadata())
    assert len(list(SnapshotReader(final).iter_pages())) == 2


def test_error_records_do_not_affect_inventory_hash(tmp_path):
    final_a = tmp_path / "a" / "snap"
    writer = SnapshotWriter(final_a)
    page = make_page()
    rel, digest = writer.write_raw_content(page.source_page_id, b"<xml/>", ".xml")
    page.raw_content_path = rel
    page.raw_content_sha256 = digest
    writer.write_record(page)
    writer.add_error(ErrorRecord(scope="page", message="boom"))
    writer.finalize(make_manifest(), make_metadata())

    final_b = write_snapshot(tmp_path / "b")
    assert (
        SnapshotReader(final_a).manifest.inventory_hash
        == SnapshotReader(final_b).manifest.inventory_hash
    )
    errors = list(SnapshotReader(final_a).iter_errors())
    assert len(errors) == 1
    assert errors[0].message == "boom"


def test_read_relative_rejects_escape(tmp_path):
    final = write_snapshot(tmp_path)
    reader = SnapshotReader(final)
    with pytest.raises(SnapshotError):
        reader.read_relative("../outside.txt")


def test_notes_and_pages_are_separated(tmp_path):
    final = tmp_path / "snap"
    writer = SnapshotWriter(final)
    writer.write_record(make_page())
    writer.write_record(NoteRecord(joplin_note_id="n1", title="Note"))
    writer.finalize(make_manifest(), make_metadata())
    reader = SnapshotReader(final)
    assert [p.source_page_id for p in reader.iter_pages()] == ["{page-1}"]
    assert [n.joplin_note_id for n in reader.iter_notes()] == ["n1"]


def test_resource_content_addressing(tmp_path):
    writer = SnapshotWriter(tmp_path / "snap")
    rel1, d1 = writer.write_resource(b"payload", ".bin")
    rel2, d2 = writer.write_resource(b"payload", ".bin")
    assert rel1 == rel2
    assert d1 == d2
    assert (writer.staging_dir / rel1).read_bytes() == b"payload"
