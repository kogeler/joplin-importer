import pytest

from joplin_importer.adapters.joplin.client import JoplinApiError, JoplinClient
from joplin_importer.adapters.joplin.scanner import (
    build_folder_paths,
    detect_content_format,
    extract_embedded_onenote_id,
    extract_resource_refs,
    scan_joplin,
)
from joplin_importer.models import (
    ContentFormat,
    NoteStatus,
    ResourceStatus,
    SnapshotReader,
    SnapshotWriter,
)
from joplin_importer.secretstore import Secret
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_joplin import FakeJoplinServer, make_note

RES_A = "a" * 32
RES_B = "b" * 32


@pytest.fixture
def server():
    return FakeJoplinServer(
        folders=[
            {"id": "folder-1", "parent_id": "", "title": "Imported", "deleted_time": 0},
            {"id": "folder-2", "parent_id": "folder-1", "title": "Section A", "deleted_time": 0},
        ],
        notes=[
            make_note("1" * 32, "Plain note", "Hello world", parent_id="folder-2"),
            make_note(
                "2" * 32,
                "With resource",
                f"![img](:/{RES_A}) and broken :/{RES_B}",
                parent_id="folder-2",
            ),
            make_note("3" * 32, "Trashed", "gone", deleted_time=1_600_000_200_000),
            make_note("4" * 32, "Conflict", "conflicted", is_conflict=1),
        ],
        note_resources={
            "2" * 32: [
                {
                    "id": RES_A,
                    "title": "img.png",
                    "mime": "image/png",
                    "filename": "img.png",
                    "size": 4,
                }
            ]
        },
        resource_bytes={RES_A: b"PNG!"},
    )


def make_client(server, mode=TransportMode.READ_ONLY):
    transport = HttpTransport(
        "http://joplin.test:41184",
        mode=mode,
        token=Secret(server.token),
        httpx_transport=server.transport(),
        sleep=lambda _s: None,
    )
    return JoplinClient(transport)


def run_scan(server, tmp_path):
    client = make_client(server)
    writer = SnapshotWriter(tmp_path / "joplin-snap")
    manifest = scan_joplin(
        client, writer, tool_version="0.1.0-test", snapshot_id="test-scan"
    )
    return manifest, SnapshotReader(tmp_path / "joplin-snap"), client


def test_scan_inventories_all_note_kinds(server, tmp_path):
    manifest, reader, _client = run_scan(server, tmp_path)
    notes = {n.joplin_note_id: n for n in reader.iter_notes()}
    assert len(notes) == 4
    assert notes["3" * 32].status == NoteStatus.TRASHED
    assert notes["4" * 32].status == NoteStatus.CONFLICT
    assert notes["1" * 32].status == NoteStatus.NORMAL
    assert manifest.record_counts["notes"] == 4
    assert manifest.coverage_status == "complete"


def test_scan_is_read_only(server, tmp_path):
    _manifest, _reader, client = run_scan(server, tmp_path)
    assert server.mutating_requests() == []
    assert client.transport.mutating_requests_sent() == []


def test_notebook_paths_resolved(server, tmp_path):
    _manifest, reader, _client = run_scan(server, tmp_path)
    note = next(n for n in reader.iter_notes() if n.joplin_note_id == "1" * 32)
    assert note.notebook_path == ["Imported", "Section A"]


def test_resources_downloaded_and_hashed(server, tmp_path):
    _manifest, reader, _client = run_scan(server, tmp_path)
    note = next(n for n in reader.iter_notes() if n.joplin_note_id == "2" * 32)
    ok = [r for r in note.resources if r.status == ResourceStatus.OK]
    missing = [r for r in note.resources if r.status == ResourceStatus.MISSING]
    assert len(ok) == 1
    assert ok[0].sha256 is not None
    assert reader.read_relative(ok[0].stored_path) == b"PNG!"
    assert note.image_count == 1
    # the broken :/bbb... reference stays visible
    assert len(missing) == 1
    assert missing[0].source_reference == f":/{RES_B}"


def test_pagination_is_followed(server, tmp_path):
    server.page_size = 2
    _manifest, reader, _client = run_scan(server, tmp_path)
    assert len(list(reader.iter_notes())) == 4
    note_pages = [p for m, p in server.requests if p == "/notes"]
    assert len(note_pages) >= 2


def test_one_failing_note_does_not_stop_scan(server, tmp_path):
    server.fail_resources_for.add("2" * 32)
    manifest, reader, _client = run_scan(server, tmp_path)
    ids = {n.joplin_note_id for n in reader.iter_notes()}
    assert "2" * 32 not in ids  # failed note is not silently included...
    assert len(ids) == 3
    errors = list(reader.iter_errors())
    assert len(errors) == 1  # ...it is an explicit error record
    assert errors[0].item_id == "2" * 32
    assert manifest.coverage_status == "partial"


def test_ping_failure_raises(tmp_path):
    server = FakeJoplinServer(token="other")
    client = make_client(server)
    client.transport._token = Secret("wrong")  # simulate bad token
    with pytest.raises(JoplinApiError):
        client.ping()


def test_optional_field_probe_failure_is_non_fatal_and_read_only(server):
    server.unsupported_note_fields.add("body_html")
    client = make_client(server)

    assert client.probe_capabilities() == {"note.body_html": False}
    assert server.mutating_requests() == []


def test_detect_content_format():
    assert detect_content_format(1, "plain **md**") == ContentFormat.MARKDOWN
    assert detect_content_format(1, "md with <b>html</b>") == ContentFormat.MIXED
    assert detect_content_format(2, "<p>html</p>") == ContentFormat.HTML
    assert detect_content_format(None, "???") == ContentFormat.UNKNOWN


def test_extract_embedded_onenote_id():
    assert (
        extract_embedded_onenote_id("onenote://page/%7Babc%7D", "")
        == "{abc}"
    )
    assert (
        extract_embedded_onenote_id(
            "", "x <!-- joplin-importer:onenote_page_id={def} --> y"
        )
        == "{def}"
    )
    assert (
        extract_embedded_onenote_id("", "x <!-- ojr:onenote_page_id={def} --> y")
        == "{def}"
    )
    assert extract_embedded_onenote_id("https://example.com", "no marker") is None


def test_extract_resource_refs_dedupes_and_lowercases():
    body = f"![a](:/{RES_A}) ![A](:/{RES_A.upper()}) [f](:/{RES_B})"
    assert extract_resource_refs(body) == [RES_A, RES_B]


def test_build_folder_paths_handles_cycles():
    folders = [
        {"id": "a", "parent_id": "b", "title": "A"},
        {"id": "b", "parent_id": "a", "title": "B"},
    ]
    paths = build_folder_paths(folders)
    assert paths["a"]  # terminates despite the cycle
