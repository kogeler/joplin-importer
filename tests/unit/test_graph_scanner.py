"""Graph adapter tests."""

import pytest

from joplin_importer.adapters.onenote_graph.client import GRAPH_BASE_URL, GraphClient
from joplin_importer.adapters.onenote_graph.scanner import scan_onenote_graph
from joplin_importer.models import (
    AuditRole,
    ContentFormat,
    SnapshotReader,
    SnapshotWriter,
    SourceBackend,
)
from joplin_importer.normalization import Normalizer
from joplin_importer.secretstore import Secret
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_graph import IMG_TAG, PAGE_HTML, FakeGraphServer, make_graph_page

RES_ID = "res-0001"


@pytest.fixture
def server():
    return FakeGraphServer(
        notebooks=[{"id": "nb1", "displayName": "Work"}],
        sections={
            "nb1": [{"id": "sec1", "displayName": "Tasks"}],
            "sg2": [{"id": "sec2", "displayName": "Old"}],
        },
        section_groups={
            "nb1": [{"id": "sg1", "displayName": "Archive"}],
            "sg1": [{"id": "sg2", "displayName": "2023"}],
        },
        pages={
            "sec1": [
                make_graph_page("gp1", "Задачи недели", order=0),
                make_graph_page("gp2", "Subtask detail", level=1, order=1),
            ],
            "sec2": [make_graph_page("gp3", "Old note", order=0)],
        },
        page_html={
            "gp1": PAGE_HTML.format(
                title="Задачи недели",
                text="Сделать отчёт",
                extra=IMG_TAG.format(rid=RES_ID),
            ),
            "gp2": PAGE_HTML.format(title="Subtask detail", text="Details here", extra=""),
            "gp3": PAGE_HTML.format(title="Old note", text="Archived", extra=""),
        },
        resources={RES_ID: b"\x89PNG fake"},
    )


def make_client(server):
    transport = HttpTransport(
        GRAPH_BASE_URL,
        mode=TransportMode.READ_ONLY,
        token=Secret(server.token),
        token_in="header",
        httpx_transport=server.transport(),
        sleep=lambda _s: None,
    )
    return GraphClient(transport)


def run_scan(server, tmp_path, **kwargs):
    writer = SnapshotWriter(tmp_path / "graph-snap")
    manifest = scan_onenote_graph(
        "client-id-unused",
        writer,
        tool_version="0.1.0-test",
        snapshot_id="graph-test",
        client=make_client(server),
        normalizer=Normalizer(),
        account_label="user@example.com",
        **kwargs,
    )
    return manifest, SnapshotReader(tmp_path / "graph-snap")


def test_scan_traverses_notebooks_groups_sections(server, tmp_path):
    manifest, reader = run_scan(server, tmp_path)
    pages = {p.source_page_id: p for p in reader.iter_pages()}
    assert len(pages) == 3
    assert pages["gp3"].section_group_path == ["Archive", "2023"]
    assert pages["gp1"].notebook_title == "Work"
    assert pages["gp1"].audit_role == AuditRole.CORROBORATING
    assert pages["gp1"].source_backend == SourceBackend.ONENOTE_GRAPH
    assert pages["gp1"].raw_content_format == ContentFormat.HTML
    assert pages["gp2"].page_level == 2  # graph level 1 -> our level 2
    assert manifest.record_counts["pages"] == 3


def test_resources_downloaded_and_mapped(server, tmp_path):
    _manifest, reader = run_scan(server, tmp_path)
    page = next(p for p in reader.iter_pages() if p.source_page_id == "gp1")
    assert page.image_count == 1
    ok = [r for r in page.resources if r.sha256]
    assert len(ok) == 1
    assert reader.read_relative(ok[0].stored_path) == b"\x89PNG fake"
    # semantic model resolves the image to its content hash
    import json

    model = json.loads(reader.read_relative(page.semantic_model_path))
    images = [n for n in model["children"] if n["kind"] == "image"]
    assert images and images[0]["hash"] == ok[0].sha256


def test_normalized_text_extracted_from_html(server, tmp_path):
    _manifest, reader = run_scan(server, tmp_path)
    page = next(p for p in reader.iter_pages() if p.source_page_id == "gp1")
    assert "Сделать отчёт" in page.normalized_text


def test_pagination_followed(server, tmp_path):
    server.page_size = 1
    _manifest, reader = run_scan(server, tmp_path)
    assert len(list(reader.iter_pages())) == 3
    listing_requests = [p for m, p in server.requests if p.endswith("/pages")]
    assert len(listing_requests) >= 2  # nextLink was followed


def test_throttling_is_retried(server, tmp_path):
    server.throttle_next = 2
    manifest, reader = run_scan(server, tmp_path)
    assert manifest.coverage_status == "complete"
    assert len(list(reader.iter_pages())) == 3


def test_one_failing_page_does_not_stop_scan(server, tmp_path):
    server.fail_content_for.add("gp2")
    manifest, reader = run_scan(server, tmp_path)
    ids = {p.source_page_id for p in reader.iter_pages()}
    assert ids == {"gp1", "gp3"}
    errors = list(reader.iter_errors())
    assert len(errors) == 1
    assert errors[0].item_id == "gp2"
    assert manifest.coverage_status == "partial"


def test_scan_is_read_only(server, tmp_path):
    run_scan(server, tmp_path)
    assert all(m == "GET" for m, _p in server.requests)


def test_notebook_filter(server, tmp_path):
    manifest, _reader = run_scan(server, tmp_path, notebook_filter="Nonexistent")
    assert manifest.record_counts["pages"] == 0


def test_limitations_recorded(server, tmp_path):
    _manifest, reader = run_scan(server, tmp_path)
    metadata = reader.scan_metadata()
    assert metadata.account_scope == "user@example.com"
    assert any("corroborating" in note for note in metadata.limitations)
