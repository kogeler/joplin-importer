import sys

import pytest

from joplin_importer.adapters.onenote_com.api import (
    ComOneNoteApi,
    OneNoteApiError,
    OneNoteProcessUnavailableError,
    _connect_onenote,
)
from joplin_importer.adapters.onenote_com.hierarchy import parse_hierarchy
from joplin_importer.adapters.onenote_com.page_parser import parse_page_xml
from joplin_importer.adapters.onenote_com.quarantine import (
    OneNoteQuarantine,
    QuarantinedPage,
    QuarantineError,
    load_quarantine,
)
from joplin_importer.adapters.onenote_com.scanner import scan_onenote_com
from joplin_importer.models import SnapshotReader, SnapshotWriter
from tests.fake_onenote import FIXTURES, FakeOneNoteApi, default_pages, page_xml

HIERARCHY = (FIXTURES / "hierarchy.xml").read_text(encoding="utf-8")


# -- hierarchy parsing --------------------------------------------------------


def test_parse_hierarchy_pages_and_paths():
    hierarchy = parse_hierarchy(HIERARCHY)
    pages = {p.page_id: p for p in hierarchy.pages}
    assert len(pages) == 5
    assert hierarchy.notebook_count == 1
    assert hierarchy.section_count == 3

    tasks_1 = pages["{page-tasks-1}"]
    assert tasks_1.title == "Задачи недели"
    assert tasks_1.notebook_title == "Work"
    assert tasks_1.section_title == "Tasks"
    assert tasks_1.section_group_path == []
    assert tasks_1.level == 1
    assert tasks_1.order == 0

    subpage = pages["{page-tasks-2}"]
    assert subpage.level == 2
    assert subpage.order == 1

    old = pages["{page-old-1}"]
    assert old.section_group_path == ["Archive", "2023"]

    trash = pages["{page-trash-1}"]
    assert trash.in_recycle_bin


def test_parse_hierarchy_warns_on_unknown_namespace():
    xml = HIERARCHY.replace("/2013/", "/2010/")
    hierarchy = parse_hierarchy(xml)
    assert any("namespace" in w for w in hierarchy.warnings)


def test_parse_hierarchy_skips_pages_without_id():
    xml = HIERARCHY.replace('ID="{page-tasks-3}"', 'ID=""')
    hierarchy = parse_hierarchy(xml)
    assert len(hierarchy.pages) == 4
    assert any("without ID" in w for w in hierarchy.warnings)


# -- page parsing --------------------------------------------------------------


def test_parse_page_with_embedded_image():
    parsed = parse_page_xml(page_xml("{p}", "T", with_image=True))
    images = [r for r in parsed.resources if r.kind == "image"]
    assert len(images) == 1
    assert images[0].data is not None
    assert images[0].data.startswith(b"\x89PNG")
    assert images[0].media_type == "image/png"


def test_parse_page_image_without_data_warns():
    import re

    xml = re.sub(
        r"<one:Data>.*?</one:Data>",
        "<one:Data></one:Data>",
        page_xml("{p}", "T", with_image=True),
    )
    parsed = parse_page_xml(xml)
    images = [r for r in parsed.resources if r.kind == "image"]
    assert len(images) == 1
    assert images[0].data is None
    assert images[0].warnings


def test_parse_page_inserted_file_reads_cache(tmp_path):
    cached = tmp_path / "report.pdf"
    cached.write_bytes(b"%PDF-fake")
    parsed = parse_page_xml(page_xml("{p}", "T", with_file_path=str(cached)))
    files = [r for r in parsed.resources if r.kind == "file"]
    assert len(files) == 1
    assert files[0].data == b"%PDF-fake"
    assert files[0].filename == "report.pdf"


def test_parse_page_inserted_file_missing_cache_warns(tmp_path):
    parsed = parse_page_xml(
        page_xml("{p}", "T", with_file_path=str(tmp_path / "gone.pdf"))
    )
    files = [r for r in parsed.resources if r.kind == "file"]
    assert files[0].data is None
    assert any("unreadable" in w for w in files[0].warnings)


def test_parse_page_records_unsupported_ink():
    parsed = parse_page_xml(page_xml("{p}", "T", with_ink=True))
    assert parsed.unsupported == {"InkDrawing": 1}
    assert any("InkDrawing" in w for w in parsed.warnings)


# -- scanner ---------------------------------------------------------------------


def run_scan(tmp_path, api, **kwargs):
    writer = SnapshotWriter(tmp_path / "onenote-snap")
    manifest = scan_onenote_com(
        api, writer, tool_version="0.1.0-test", snapshot_id="com-test", **kwargs
    )
    return manifest, SnapshotReader(tmp_path / "onenote-snap")


def test_scan_captures_pages_and_resources(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    manifest, reader = run_scan(tmp_path, api)
    pages = {p.source_page_id: p for p in reader.iter_pages()}
    assert len(pages) == 4  # recycle bin excluded by default
    assert "{page-trash-1}" not in pages

    with_image = pages["{page-tasks-1}"]
    assert with_image.image_count == 1
    assert with_image.resource_hashes
    assert with_image.section_group_path == []
    assert with_image.created_at == "2025-01-01T08:00:00Z"

    old = pages["{page-old-1}"]
    assert old.section_group_path == ["Archive", "2023"]
    assert any("InkDrawing" in w for w in old.warnings)
    assert manifest.record_counts["pages"] == 4
    assert manifest.coverage_status == "complete"
    assert any("recycle-bin" in n for n in manifest.coverage_notes)


def test_scan_includes_recycle_bin_when_asked(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    _manifest, reader = run_scan(tmp_path, api, include_recycle_bin=True)
    pages = {p.source_page_id: p for p in reader.iter_pages()}
    assert "{page-trash-1}" in pages
    assert any("recycle bin" in w for w in pages["{page-trash-1}"].warnings)


def test_scan_continues_after_page_failure(tmp_path):
    api = FakeOneNoteApi(pages=default_pages(), fail_pages={"{page-tasks-2}"})
    manifest, reader = run_scan(tmp_path, api)
    ids = {p.source_page_id for p in reader.iter_pages()}
    assert "{page-tasks-2}" not in ids
    assert len(ids) == 3
    errors = list(reader.iter_errors())
    assert len(errors) == 1
    assert errors[0].item_id == "{page-tasks-2}"
    assert "0x80042010" in errors[0].message
    assert manifest.coverage_status == "partial"


def test_scan_stops_after_native_onenote_process_failure(tmp_path):
    api = FakeOneNoteApi(
        pages=default_pages(), process_crash_pages={"{page-tasks-2}"}
    )

    manifest, reader = run_scan(tmp_path, api)

    assert "GetPageContent:{page-tasks-1}" in api.calls
    assert "GetPageContent:{page-tasks-2}" in api.calls
    assert "GetPageContent:{page-tasks-3}" not in api.calls
    assert manifest.record_counts["pages"] == 1
    assert manifest.record_counts["unattempted_after_process_failure"] == 2
    errors = list(reader.iter_errors())
    assert len(errors) == 1
    assert errors[0].exception_type == "OneNoteProcessUnavailableError"
    assert any("stopped without attempting 2" in note for note in manifest.coverage_notes)


def test_scan_quarantines_page_before_get_page_content(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    quarantine = OneNoteQuarantine(
        pages=[
            QuarantinedPage(
                page_id="{page-tasks-2}",
                expected_title="Subtask detail",
                reason="known native OneNote crash",
            )
        ]
    )

    manifest, reader = run_scan(tmp_path, api, quarantine=quarantine)

    assert "GetPageContent:{page-tasks-2}" not in api.calls
    assert manifest.coverage_status == "partial"
    assert manifest.record_counts["quarantined_pages"] == 1
    assert manifest.configuration["quarantine_digest"] == quarantine.digest()
    errors = list(reader.iter_errors())
    assert len(errors) == 1
    assert errors[0].item_id == "{page-tasks-2}"
    assert errors[0].exception_type == "IntentionallyQuarantined"
    assert "known native OneNote crash" in errors[0].message


def test_scan_quarantine_title_mismatch_still_skips_exact_id(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    quarantine = OneNoteQuarantine(
        pages=[
            QuarantinedPage(
                page_id="{page-tasks-2}",
                expected_title="Previous title",
                reason="known native OneNote crash",
            )
        ]
    )

    manifest, reader = run_scan(tmp_path, api, quarantine=quarantine)

    assert "GetPageContent:{page-tasks-2}" not in api.calls
    assert manifest.record_counts["quarantine_title_mismatches"] == 1
    error = next(reader.iter_errors())
    assert "Previous title" in error.message
    assert "Subtask detail" in error.message


def test_scan_records_stale_quarantine_entry(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    quarantine = OneNoteQuarantine(
        pages=[
            QuarantinedPage(
                page_id="{page-no-longer-exists}",
                expected_title="Old page",
                reason="old crash report",
            )
        ]
    )

    manifest, reader = run_scan(tmp_path, api, quarantine=quarantine)

    assert manifest.record_counts["pages"] == 4
    assert manifest.record_counts["stale_quarantine_entries"] == 1
    assert manifest.coverage_status == "partial"
    error = next(reader.iter_errors())
    assert error.exception_type == "StaleQuarantineEntry"
    assert error.item_id == "{page-no-longer-exists}"


def test_load_quarantine_validates_duplicate_ids(tmp_path):
    path = tmp_path / "quarantine.json"
    path.write_text(
        """{
          "schema_version": 1,
          "pages": [
            {"page_id": "{p}", "expected_title": "T", "reason": "first"},
            {"page_id": "{p}", "expected_title": "T", "reason": "second"}
          ]
        }""",
        encoding="utf-8",
    )

    with pytest.raises(QuarantineError, match="duplicate quarantine page_id"):
        load_quarantine(path)


def test_quarantine_preserves_title_whitespace_for_exact_diagnostic_match():
    entry = QuarantinedPage(page_id=" {p} ", expected_title="Title ", reason=" test ")

    assert entry.page_id == "{p}"
    assert entry.expected_title == "Title "
    assert entry.reason == "test"


def test_scan_is_read_only_calls(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    run_scan(tmp_path, api)
    assert all(
        c == "GetHierarchy" or c.startswith("GetPageContent:") for c in api.calls
    )


def test_notebook_filter(tmp_path):
    api = FakeOneNoteApi(pages=default_pages())
    manifest, reader = run_scan(tmp_path, api, notebook_filter="Nonexistent")
    assert manifest.record_counts["pages"] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only negative test")
def test_com_api_refuses_non_windows():
    with pytest.raises(OneNoteApiError, match="Windows"):
        ComOneNoteApi()


def test_com_connection_falls_back_to_compatible_progid():
    class GoodApplication:
        def GetHierarchy(self):
            pass

        def GetPageContent(self):
            pass

    good = GoodApplication()
    attempts = []

    class GenCache:
        @staticmethod
        def EnsureDispatch(progid):
            attempts.append(("early", progid))
            if progid == "OneNote.Application.12":
                return good
            raise TypeError("broken type-library registration")

    class Client:
        gencache = GenCache()

        @staticmethod
        def Dispatch(progid):
            attempts.append(("late", progid))
            return object()

    assert _connect_onenote(Client()) is good
    assert attempts[-1] == ("early", "OneNote.Application.12")


def test_com_page_read_does_not_retry_after_rpc_process_failure():
    class RpcFailure(Exception):
        hresult = -2147023170

    class CrashedApplication:
        def __init__(self):
            self.calls = 0

        def GetPageContent(self, *_args):
            self.calls += 1
            raise RpcFailure("RPC call failed")

    application = CrashedApplication()
    api = ComOneNoteApi.__new__(ComOneNoteApi)
    api._app = application

    with pytest.raises(OneNoteProcessUnavailableError, match="lost the OneNote COM process"):
        api.get_page_content("{page}", include_binary=True)

    assert application.calls == 1
