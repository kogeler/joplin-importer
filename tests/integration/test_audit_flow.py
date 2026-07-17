"""End-to-end audit: COM-like scan + Joplin scan -> matches and findings."""

import pytest

from joplin_importer.adapters.joplin.client import JoplinClient
from joplin_importer.adapters.joplin.scanner import scan_joplin
from joplin_importer.adapters.onenote_com.scanner import scan_onenote_com
from joplin_importer.matching.audit import run_audit
from joplin_importer.models import (
    FindingKind,
    MatchConfidence,
    SnapshotReader,
    SnapshotWriter,
)
from joplin_importer.normalization import Normalizer
from joplin_importer.secretstore import Secret
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_joplin import FakeJoplinServer, make_note
from tests.fake_onenote import FakeOneNoteApi, default_pages


@pytest.fixture
def snapshots(tmp_path):
    normalizer = Normalizer()

    # source: fake OneNote with 4 pages (see tests/fixtures/onenote/hierarchy.xml)
    api = FakeOneNoteApi(pages=default_pages())
    source_writer = SnapshotWriter(tmp_path / "source")
    scan_onenote_com(
        api,
        source_writer,
        tool_version="0.1.0-test",
        snapshot_id="src-1",
        normalizer=normalizer,
    )

    # target: Joplin got only 2 of them; one is empty with a placeholder title
    server = FakeJoplinServer(
        folders=[
            {"id": "f1", "parent_id": "", "title": "Work", "deleted_time": 0},
            {"id": "f2", "parent_id": "f1", "title": "Tasks", "deleted_time": 0},
        ],
        notes=[
            make_note(
                "1" * 32,
                "Задачи недели",
                "Сделать отчёт",
                parent_id="f2",
                user_created_time=1_735_718_400_000,  # 2025-01-01T08:00:00Z
            ),
            make_note(
                "2" * 32,
                "Untitled Page",
                "",
                parent_id="f2",
                user_created_time=1_735_804_800_000,  # 2025-01-02T08:00:00Z
            ),
        ],
    )
    transport = HttpTransport(
        "http://joplin.test:41184",
        mode=TransportMode.READ_ONLY,
        token=Secret(server.token),
        httpx_transport=server.transport(),
        sleep=lambda _s: None,
    )
    target_writer = SnapshotWriter(tmp_path / "target")
    scan_joplin(
        JoplinClient(transport),
        target_writer,
        tool_version="0.1.0-test",
        snapshot_id="tgt-1",
        normalizer=normalizer,
    )
    return SnapshotReader(tmp_path / "source"), SnapshotReader(tmp_path / "target")


def test_audit_end_to_end(snapshots):
    source, target = snapshots
    result = run_audit(source, target, tool_version="0.1.0-test")

    assert result.summary.source_pages == 4
    assert result.summary.target_notes == 2
    assert result.summary.source_manifest_hash
    assert result.summary.threshold_version

    by_page = {m.source_page_id: m for m in result.matches if m.source_page_id}
    # the two imported pages must be found
    assert by_page["{page-tasks-1}"].confidence in (
        MatchConfidence.EXACT,
        MatchConfidence.HIGH_CONFIDENCE,
        MatchConfidence.PROBABLE,
    )
    # pages that never made it to Joplin are reported missing
    kinds = {f.kind for f in result.findings}
    assert FindingKind.SOURCE_PAGE_MISSING in kinds

    missing_ids = {
        f.source_page_id for f in result.findings if f.kind == FindingKind.SOURCE_PAGE_MISSING
    }
    assert "{page-old-1}" in missing_ids


def test_audit_result_serializes(snapshots):
    source, target = snapshots
    result = run_audit(source, target, tool_version="0.1.0-test")
    payload = result.model_dump_json()
    assert "source_pages" in payload
