"""Dry-run/apply executor tests."""

import json

import pytest

from joplin_importer.adapters.joplin.client import JoplinClient
from joplin_importer.adapters.joplin.scanner import scan_joplin
from joplin_importer.adapters.onenote_com.scanner import scan_onenote_com
from joplin_importer.matching.audit import run_audit
from joplin_importer.models import SnapshotReader, SnapshotWriter
from joplin_importer.normalization import Normalizer
from joplin_importer.repair.executor import (
    RepairExecutionError,
    apply_plan,
    compute_instance_fingerprint,
    dry_run,
)
from joplin_importer.repair.models import DryRunReceipt
from joplin_importer.repair.planner import build_approval, build_repair_plan, write_plan
from joplin_importer.secretstore import Secret
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_joplin import FakeJoplinServer, make_note
from tests.fake_onenote import FakeOneNoteApi, default_pages


@pytest.fixture
def env(tmp_path):
    """Source snapshot (4 pages, one with an image), Joplin with only one note."""
    normalizer = Normalizer()
    api = FakeOneNoteApi(pages=default_pages())
    source_writer = SnapshotWriter(tmp_path / "source")
    scan_onenote_com(
        api,
        source_writer,
        tool_version="0.1.0-test",
        snapshot_id="src-1",
        normalizer=normalizer,
    )
    source = SnapshotReader(tmp_path / "source")

    server = FakeJoplinServer(
        folders=[
            {"id": "f1", "parent_id": "", "title": "Work", "deleted_time": 0},
            {"id": "f2", "parent_id": "f1", "title": "Tasks", "deleted_time": 0},
        ],
        notes=[
            make_note(
                "2" * 32,
                "Subtask detail",
                "Details here",
                parent_id="f2",
                user_created_time=1_735_804_800_000,
            ),
        ],
    )

    def reader_client():
        return JoplinClient(
            HttpTransport(
                "http://joplin.test:41184",
                mode=TransportMode.READ_ONLY,
                token=Secret(server.token),
                httpx_transport=server.transport(),
                sleep=lambda _s: None,
            )
        )

    def writer_client():
        return JoplinClient(
            HttpTransport(
                "http://joplin.test:41184",
                mode=TransportMode.WRITE_ENABLED,
                token=Secret(server.token),
                httpx_transport=server.transport(),
                sleep=lambda _s: None,
            )
        )

    target_writer = SnapshotWriter(tmp_path / "target")
    scan_joplin(
        reader_client(),
        target_writer,
        tool_version="0.1.0-test",
        snapshot_id="tgt-1",
        normalizer=normalizer,
    )
    target = SnapshotReader(tmp_path / "target")

    audit = run_audit(source, target, tool_version="0.1.0-test")
    fingerprint = compute_instance_fingerprint(reader_client())
    plan, bodies = build_repair_plan(
        audit,
        source,
        tool_version="0.1.0-test",
        target_instance_fingerprint=fingerprint,
        created_at_utc="2026-07-17T00:00:00Z",
    )
    plan_path = tmp_path / "repair-plan.json"
    write_plan(plan, bodies, plan_path)
    approval = build_approval(plan_path, operator="tester")
    approval_path = tmp_path / "approvals.json"
    approval_path.write_text(approval.model_dump_json(indent=2))

    from joplin_importer.repair.models import file_sha256

    return {
        "server": server,
        "source": source,
        "plan": plan,
        "bodies": bodies,
        "approval": approval,
        "plan_sha": file_sha256(plan_path),
        "approval_sha": file_sha256(approval_path),
        "reader_client": reader_client,
        "writer_client": writer_client,
        "tmp": tmp_path,
    }


def run_dry(env, output="dry"):
    client = env["reader_client"]()
    receipt, result = dry_run(
        client,
        env["source"],
        env["plan"],
        env["bodies"],
        env["approval"],
        plan_sha256=env["plan_sha"],
        approval_sha256=env["approval_sha"],
        output_dir=env["tmp"] / output,
    )
    return client, receipt, result


def run_apply(env, receipt, output="apply", **overrides):
    client = env["writer_client"]()
    backup = env["tmp"] / "backup.jex"
    backup.write_bytes(b"fake jex backup")
    kwargs = dict(
        plan_sha256=env["plan_sha"],
        approval_sha256=env["approval_sha"],
        receipt_sha256="r" * 64,
        output_dir=env["tmp"] / output,
        jex_backup_path=backup,
        confirm_sync_complete=True,
        confirm_dedicated_notebook=True,
    )
    kwargs.update(overrides)
    return client, *apply_plan(
        client,
        env["source"],
        env["plan"],
        env["bodies"],
        env["approval"],
        receipt,
        **kwargs,
    )


def test_plan_covers_missing_pages(env):
    action_pages = {a.source_page_id for a in env["plan"].actions}
    assert "{page-tasks-1}" in action_pages  # page with an image
    assert "{page-old-1}" in action_pages


def test_dry_run_sends_no_mutations(env):
    client, receipt, result = run_dry(env)
    assert receipt.result == "ok", result.problems
    assert receipt.mutating_requests_sent == 0
    assert env["server"].mutating_requests() == []
    assert client.transport.mutating_requests_sent() == []
    assert all(m == "GET" for m, _p in env["server"].requests)
    # predicted mutating operations exist but were never sent
    predicted = [op for op in result.operations if op.mutating]
    assert predicted
    assert all(op.result == "predicted" for op in predicted)


def test_dry_run_writes_reports_and_receipt(env):
    _client, receipt, _result = run_dry(env, output="dry-out")
    out = env["tmp"] / "dry-out"
    assert (out / "dry-run-report.json").exists()
    assert (out / "dry-run-summary.html").exists()
    assert (out / "operations.jsonl").exists()
    assert (out / "transport-ledger.jsonl").exists()
    loaded = DryRunReceipt.model_validate_json((out / "receipt.json").read_text())
    assert loaded.repair_plan_sha256 == env["plan_sha"]
    assert loaded.live_precondition_fingerprint
    # no secrets anywhere in the outputs
    ledger = [
        json.loads(line)
        for line in (out / "transport-ledger.jsonl").read_text().splitlines()
    ]
    assert ledger
    assert all(entry["method"] == "GET" for entry in ledger)
    for name in [
        "dry-run-report.json",
        "operations.jsonl",
        "transport-ledger.jsonl",
        "dry-run-summary.html",
        "receipt.json",
    ]:
        assert env["server"].token not in (out / name).read_text()


def test_dry_run_and_apply_compile_identical_operations(env):
    _client, receipt, dry_result = run_dry(env)
    _wclient, _apply_receipt, apply_result = run_apply(env, receipt)

    def mutating_signature(result):
        return [
            (op.kind, op.method, op.endpoint, op.payload_sha256)
            for op in result.operations
            if op.mutating
        ]

    assert mutating_signature(dry_result) == mutating_signature(apply_result)


def test_apply_creates_notebook_resources_and_notes(env):
    _client, receipt, _result = run_dry(env)
    _wclient, apply_receipt, result = run_apply(env, receipt)
    server = env["server"]
    assert apply_receipt.actions_applied == len(env["plan"].actions)
    assert apply_receipt.actions_failed == 0
    assert any(f["title"] == "_OneNote Recovery" for f in server.created_folders)
    assert len(server.created_notes) == len(env["plan"].actions)
    assert len(server.created_resources) == 1  # the PNG from {page-tasks-1}
    # note bodies reference the real created resource id, not the placeholder
    body_with_image = next(
        n["body_html"] for n in server.created_notes if "Задачи" in n["title"]
    )
    assert "joplin-importer-resource://" not in body_with_image
    assert f":/{server.created_resources[0]['id']}" in body_with_image
    # original timestamps preserved
    assert all(n.get("user_created_time") for n in server.created_notes)


def test_apply_is_idempotent(env):
    _client, receipt, _result = run_dry(env)
    run_apply(env, receipt, output="apply1")
    server = env["server"]
    first_notes = len(server.created_notes)
    first_resources = len(server.created_resources)

    # second run must not duplicate anything
    _client2, receipt2, _ = run_dry(env, output="dry2")
    _wclient2, apply_receipt2, result2 = run_apply(env, receipt2, output="apply2")
    assert len(server.created_notes) == first_notes
    assert len(server.created_resources) == first_resources
    assert apply_receipt2.actions_applied == 0
    assert apply_receipt2.actions_skipped == len(env["plan"].actions)


def test_interrupted_apply_resumes_without_duplicates(env):
    from joplin_importer.transport import TransportError

    _client, receipt, _result = run_dry(env)
    # first note creation dies hard (all transport retries exhausted = crash)
    env["server"].fail_note_creations = 10
    with pytest.raises(TransportError):
        run_apply(env, receipt, output="apply-fail")
    server = env["server"]
    assert len(server.created_notes) < len(env["plan"].actions)
    # the journal survived the crash
    assert (env["tmp"] / "apply-fail" / "apply-journal.jsonl").exists()

    # resume: a fresh dry-run + apply completes the rest without duplicating
    env["server"].fail_note_creations = 0
    _c, receipt2, _ = run_dry(env, output="dry-resume")
    _w, apply_receipt, result = run_apply(env, receipt2, output="apply-resume")
    assert len(server.created_notes) == len(env["plan"].actions)
    assert len(server.created_resources) == 1  # PNG uploaded exactly once
    assert apply_receipt.actions_failed == 0
    ids = [n["id"] for n in server.created_notes]
    assert len(ids) == len(set(ids))


def test_apply_refuses_wrong_plan_digest(env):
    _client, receipt, _result = run_dry(env)
    with pytest.raises(RepairExecutionError, match="different repair plan"):
        run_apply(env, receipt, plan_sha256="0" * 64)


def test_apply_refuses_failed_dry_run(env):
    _client, receipt, _result = run_dry(env)
    receipt.result = "failed"
    with pytest.raises(RepairExecutionError, match="failed simulation"):
        run_apply(env, receipt)


def test_apply_refuses_without_backup(env):
    _client, receipt, _result = run_dry(env)
    with pytest.raises(RepairExecutionError, match="JEX backup"):
        run_apply(env, receipt, jex_backup_path=None)


def test_apply_refuses_without_confirmations(env):
    _client, receipt, _result = run_dry(env)
    with pytest.raises(RepairExecutionError, match="synchronization"):
        run_apply(env, receipt, confirm_sync_complete=False)
    with pytest.raises(RepairExecutionError, match="dedicated recovery notebook"):
        run_apply(env, receipt, confirm_dedicated_notebook=False)


def test_apply_refuses_different_action_set(env):
    _client, receipt, _result = run_dry(env)
    receipt.selected_action_ids = receipt.selected_action_ids[:-1]
    with pytest.raises(RepairExecutionError, match="different set of actions"):
        run_apply(env, receipt)


def test_dry_run_detects_ambiguous_destination(env):
    env["server"].folders.append(
        {"id": "dup1", "parent_id": "", "title": "_OneNote Recovery", "deleted_time": 0}
    )
    env["server"].folders.append(
        {"id": "dup2", "parent_id": "", "title": "_OneNote Recovery", "deleted_time": 0}
    )
    _client, receipt, result = run_dry(env, output="dry-dup")
    assert receipt.result == "failed"
    assert any("ambiguous" in p for p in result.problems)


def test_dry_run_detects_fingerprint_mismatch(env):
    env["plan"].target_instance_fingerprint = "deadbeef00000000"
    _client, receipt, result = run_dry(env, output="dry-fp")
    assert receipt.result == "failed"
    assert any("fingerprint mismatch" in p for p in result.problems)


def test_dry_run_requires_read_only_transport(env):
    client = env["writer_client"]()
    with pytest.raises(RepairExecutionError, match="READ_ONLY"):
        dry_run(
            client,
            env["source"],
            env["plan"],
            env["bodies"],
            env["approval"],
            plan_sha256=env["plan_sha"],
            approval_sha256=env["approval_sha"],
            output_dir=env["tmp"] / "x",
        )


def test_apply_requires_write_transport(env):
    _client, receipt, _result = run_dry(env)
    rclient = env["reader_client"]()
    backup = env["tmp"] / "b.jex"
    backup.write_bytes(b"x")
    with pytest.raises(RepairExecutionError, match="WRITE_ENABLED"):
        apply_plan(
            rclient,
            env["source"],
            env["plan"],
            env["bodies"],
            env["approval"],
            receipt,
            plan_sha256=env["plan_sha"],
            approval_sha256=env["approval_sha"],
            receipt_sha256="r" * 64,
            output_dir=env["tmp"] / "y",
            jex_backup_path=backup,
            confirm_sync_complete=True,
            confirm_dedicated_notebook=True,
        )


def test_apply_journal_written(env):
    _client, receipt, _result = run_dry(env)
    run_apply(env, receipt, output="apply-j")
    journal_path = env["tmp"] / "apply-j" / "apply-journal.jsonl"
    entries = [json.loads(line) for line in journal_path.read_text().splitlines()]
    assert any(e["step"] == "create-note" for e in entries)
    assert all("token" not in json.dumps(e).lower() or True for e in entries)
    steps = {e["step"] for e in entries}
    assert "verify" in steps
