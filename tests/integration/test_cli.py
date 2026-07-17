"""CLI tests."""

import json
import sys

import pytest
from click.testing import CliRunner

import joplin_importer.cli.main as cli_main
from joplin_importer import __version__
from joplin_importer.adapters.joplin.client import JoplinClient
from joplin_importer.cli.main import main
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_joplin import FakeJoplinServer, make_note


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def server():
    return FakeJoplinServer(
        folders=[
            {"id": "f1", "parent_id": "", "title": "Work", "deleted_time": 0},
        ],
        notes=[make_note("1" * 32, "Note", "body", parent_id="f1")],
    )


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("tok-123\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def patched_client(server, monkeypatch):
    """Route CLI-created clients to the fake server."""

    def fake_make_client(base_url, token_file, token_env, *, write=False):
        mode = TransportMode.WRITE_ENABLED if write else TransportMode.READ_ONLY
        from joplin_importer.secretstore import Secret

        return JoplinClient(
            HttpTransport(
                base_url,
                mode=mode,
                token=Secret(server.token),
                httpx_transport=server.transport(),
                sleep=lambda _s: None,
            )
        )

    monkeypatch.setattr(cli_main, "_make_client", fake_make_client)
    return server


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert f"joplin-importer, version {__version__}" in result.output


def test_help_lists_commands(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in ["doctor", "scan-onenote", "scan-joplin", "compare",
                    "export-plan", "export-approve",
                    "export-dry-run", "export-apply", "export-validate"]:
        assert command in result.output
    for legacy_command in ["plan", "approve", "dry-run", "apply"]:
        assert f"  {legacy_command} " not in result.output


def test_doctor_json(runner, patched_client, token_file):
    result = runner.invoke(
        main, ["doctor", "--token-file", str(token_file), "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ping"] == "ok"
    assert payload["mutating_requests_sent"] == 0
    assert payload["instance_fingerprint"]


def test_doctor_never_prints_token(runner, patched_client, token_file):
    result = runner.invoke(main, ["doctor", "--token-file", str(token_file)])
    assert "tok-123" not in result.output


def test_scan_joplin_writes_snapshot(runner, patched_client, token_file, tmp_path):
    out = tmp_path / "snap"
    result = runner.invoke(
        main,
        ["scan-joplin", "--token-file", str(token_file), "--output", str(out), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert (out / "manifest.json").exists()
    payload = json.loads(result.output)
    assert payload["records"]["notes"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only negative test")
def test_scan_onenote_com_fails_cleanly_off_windows(runner, tmp_path):
    result = runner.invoke(
        main,
        ["scan-onenote", "--backend", "com", "--output", str(tmp_path / "s")],
    )
    assert result.exit_code != 0
    assert "Windows" in result.output


def test_scan_onenote_com_accepts_quarantine_file(runner, monkeypatch, tmp_path):
    from joplin_importer.adapters.onenote_com import api as com_api
    from joplin_importer.models import SnapshotReader
    from tests.fake_onenote import FakeOneNoteApi, default_pages

    fake = FakeOneNoteApi(pages=default_pages())
    monkeypatch.setattr(com_api, "ComOneNoteApi", lambda: fake)
    quarantine_path = tmp_path / "quarantine.json"
    quarantine_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pages": [
                    {
                        "page_id": "{page-tasks-2}",
                        "expected_title": "Subtask detail",
                        "reason": "known native OneNote crash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "source"

    result = runner.invoke(
        main,
        [
            "scan-onenote",
            "--backend",
            "com",
            "--skip-page-file",
            str(quarantine_path),
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == cli_main.EXIT_FINDINGS, result.output
    assert "GetPageContent:{page-tasks-2}" not in fake.calls
    reader = SnapshotReader(output)
    assert reader.manifest.record_counts["quarantined_pages"] == 1
    assert next(reader.iter_errors()).exception_type == "IntentionallyQuarantined"


def test_scan_onenote_rejects_quarantine_for_graph(runner, tmp_path):
    quarantine_path = tmp_path / "quarantine.json"
    quarantine_path.write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "scan-onenote",
            "--backend",
            "graph",
            "--quarantine-file",
            str(quarantine_path),
            "--output",
            str(tmp_path / "source"),
        ],
    )

    assert result.exit_code == cli_main.EXIT_USAGE
    assert "only valid with --backend com" in result.output
    assert not (tmp_path / "source.staging").exists()


def test_scan_onenote_rejects_invalid_quarantine_before_staging(runner, tmp_path):
    quarantine_path = tmp_path / "quarantine.json"
    quarantine_path.write_text("not-json", encoding="utf-8")

    result = runner.invoke(
        main,
        [
            "scan-onenote",
            "--backend",
            "com",
            "--quarantine-file",
            str(quarantine_path),
            "--output",
            str(tmp_path / "source"),
        ],
    )

    assert result.exit_code == cli_main.EXIT_USAGE
    assert "invalid quarantine file" in result.output
    assert not (tmp_path / "source.staging").exists()


def test_scan_onenote_backup_auto_discovers_latest_sections(
    runner, monkeypatch, tmp_path
):
    from joplin_importer.adapters.onenote_backup import scanner as backup_scanner
    from joplin_importer.models import SnapshotReader
    from tests.fake_onenote_backup import Document, Outline, Page, RichText

    local = tmp_path / "local"
    backup = local / "Microsoft" / "OneNote" / "16.0" / "localized-name"
    section = backup / "Book" / "Section ( Backup 2026-07-17).one"
    section.parent.mkdir(parents=True)
    section.write_bytes(b"one")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(
        backup_scanner,
        "_load_aspose_document",
        lambda _path: Document(Page("Page", Outline(RichText("body")))),
    )
    output = tmp_path / "source"

    result = runner.invoke(
        main,
        ["scan-onenote", "--backend", "backup", "--output", str(output), "--json"],
    )

    assert result.exit_code == 0, result.output
    reader = SnapshotReader(output)
    assert reader.manifest.configuration["backup_root_mode"] == "auto"
    assert reader.manifest.configuration["backup_selection"] == "latest"
    assert [page.page_title for page in reader.iter_pages()] == ["Page"]


def test_scan_onenote_rejects_backup_root_for_other_backends(runner, tmp_path):
    backup = tmp_path / "backup"
    (backup / "Book").mkdir(parents=True)
    (backup / "Book" / "Section.one").write_bytes(b"one")
    output = tmp_path / "source"

    result = runner.invoke(
        main,
        [
            "scan-onenote",
            "--backend",
            "com",
            "--backup-root",
            str(backup),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == cli_main.EXIT_USAGE
    assert "only valid with --backend backup" in result.output
    assert not output.with_name(output.name + ".staging").exists()


def test_missing_token_is_usage_error(runner, tmp_path):
    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == cli_main.EXIT_USAGE
    assert "token" in result.output


def test_compare_pipeline_is_analysis_only(runner, patched_client, token_file, tmp_path):
    """Compare writes reports, while legacy merge commands stay unavailable."""
    from joplin_importer.adapters.onenote_com.scanner import scan_onenote_com
    from joplin_importer.models import SnapshotWriter
    from joplin_importer.normalization import Normalizer
    from tests.fake_onenote import FakeOneNoteApi, default_pages

    source_dir = tmp_path / "source"
    scan_onenote_com(
        FakeOneNoteApi(pages=default_pages()),
        SnapshotWriter(source_dir),
        tool_version="t",
        snapshot_id="s",
        normalizer=Normalizer(),
    )
    target_dir = tmp_path / "target"
    result = runner.invoke(
        main,
        ["scan-joplin", "--token-file", str(token_file), "--output", str(target_dir)],
    )
    assert result.exit_code == 0, result.output

    audit_dir = tmp_path / "audit"
    result = runner.invoke(
        main,
        ["compare", str(source_dir), str(target_dir), "--output", str(audit_dir)],
    )
    assert result.exit_code == cli_main.EXIT_FINDINGS  # findings exist
    assert (audit_dir / "summary.html").exists()
    assert (audit_dir / "matches.csv").exists()
    assert (audit_dir / "extractor-errors.csv").exists()

    for legacy_command in ["plan", "approve", "dry-run", "apply"]:
        result = runner.invoke(main, [legacy_command, "--help"])
        assert result.exit_code == cli_main.EXIT_USAGE
        assert "No such command" in result.output
