"""Complete add-only/replace-managed export workflow tests."""

from __future__ import annotations

import json

import pytest

from joplin_importer.adapters.joplin.client import JoplinClient
from joplin_importer.adapters.joplin.scanner import scan_joplin
from joplin_importer.adapters.onenote_com.scanner import scan_onenote_com
from joplin_importer.exporting.executor import (
    ExportExecutionError,
    apply_export,
    dry_run_export,
    export_instance_fingerprint,
)
from joplin_importer.exporting.planner import (
    build_export_approval,
    build_export_plan,
    write_export_plan,
)
from joplin_importer.exporting.validation import validate_export
from joplin_importer.models import SnapshotReader, SnapshotWriter, SourceBackend
from joplin_importer.normalization import Normalizer
from joplin_importer.repair.models import file_sha256
from joplin_importer.secretstore import Secret
from joplin_importer.transport import HttpTransport, TransportMode
from tests.fake_joplin import FakeJoplinServer
from tests.fake_onenote import FakeOneNoteApi, default_pages


@pytest.fixture
def export_env(tmp_path):
    writer = SnapshotWriter(tmp_path / "source")
    scan_onenote_com(
        FakeOneNoteApi(pages=default_pages()),
        writer,
        tool_version="0.1.0-test",
        snapshot_id="export-source",
        normalizer=Normalizer(),
    )
    source = SnapshotReader(tmp_path / "source")
    server = FakeJoplinServer()

    def client(mode=TransportMode.READ_ONLY):
        return JoplinClient(
            HttpTransport(
                "http://joplin.test:41184",
                mode=mode,
                token=Secret(server.token),
                httpx_transport=server.transport(),
                sleep=lambda _seconds: None,
            )
        )

    return source, server, client, tmp_path


def _bundle(source, client_factory, tmp_path, *, policy="fail", suffix="one"):
    fingerprint = export_instance_fingerprint(client_factory())
    plan, bodies = build_export_plan(
        source,
        tool_version="0.1.0-test",
        conflict_policy=policy,
        target_instance_fingerprint=fingerprint,
        created_at_utc="2026-07-17T00:00:00Z",
    )
    plan_path = tmp_path / f"export-plan-{suffix}.json"
    write_export_plan(plan, bodies, plan_path)
    approval = build_export_approval(plan_path, operator="tester")
    approval_path = tmp_path / f"export-approval-{suffix}.json"
    approval_path.write_text(approval.model_dump_json(indent=2), encoding="utf-8")
    return {
        "plan": plan,
        "bodies": bodies,
        "approval": approval,
        "plan_sha": file_sha256(plan_path),
        "approval_sha": file_sha256(approval_path),
    }


def test_export_plan_rejects_experimental_graph_source(export_env):
    source, _server, _client_factory, _tmp_path = export_env
    source.manifest.source_backend = SourceBackend.ONENOTE_GRAPH

    with pytest.raises(ValueError, match="experimental analysis"):
        build_export_plan(source, tool_version="test")


def _dry(source, client_factory, tmp_path, bundle, suffix="one"):
    return dry_run_export(
        client_factory(),
        source,
        bundle["plan"],
        bundle["bodies"],
        bundle["approval"],
        plan_sha256=bundle["plan_sha"],
        approval_sha256=bundle["approval_sha"],
        output_dir=tmp_path / f"dry-{suffix}",
    )


def _apply(source, client_factory, tmp_path, bundle, receipt, suffix="one"):
    backup = tmp_path / "empty-profile.jex"
    backup.write_bytes(b"JEX test backup")
    return apply_export(
        client_factory(TransportMode.WRITE_ENABLED),
        source,
        bundle["plan"],
        bundle["bodies"],
        bundle["approval"],
        receipt,
        plan_sha256=bundle["plan_sha"],
        approval_sha256=bundle["approval_sha"],
        receipt_sha256="f" * 64,
        output_dir=tmp_path / f"apply-{suffix}",
        jex_backup_path=backup,
        confirm_sync_complete=True,
        confirm_full_replace=bundle["plan"].conflict_policy == "replace-managed",
    )


def test_full_export_plan_contains_every_page_and_source_folder(export_env):
    source, _server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    plan = bundle["plan"]
    assert len(plan.notes) == 4
    assert {note.source_page_id for note in plan.notes} == {
        "{page-tasks-1}",
        "{page-tasks-2}",
        "{page-tasks-3}",
        "{page-old-1}",
    }
    assert {folder.kind for folder in plan.folders} == {"notebook", "section", "section-group"}


def test_empty_profile_dry_run_is_read_only(export_env):
    source, server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    receipt, result = _dry(source, client_factory, tmp_path, bundle)
    assert receipt.result == "ok", result.problems
    assert receipt.mutating_requests_sent == 0
    assert server.mutating_requests() == []
    assert sum(op.kind == "create-export-note" for op in result.operations) == 4
    assert all(op.result == "predicted" for op in result.operations if op.mutating)


def test_unmanaged_root_name_conflict_fails_before_writes(export_env):
    source, server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    root_title = next(
        folder.title for folder in bundle["plan"].folders if not folder.parent_node_id
    )
    server.folders.append(
        {"id": "unmanaged-root", "parent_id": "", "title": root_title, "deleted_time": 0}
    )
    receipt, result = _dry(source, client_factory, tmp_path, bundle)
    assert receipt.result == "failed"
    assert any("unmanaged" in problem for problem in result.problems)
    assert not any(op.mutating for op in result.operations)
    assert server.mutating_requests() == []


def test_apply_creates_complete_tree_and_is_idempotent(export_env):
    source, server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    receipt, _ = _dry(source, client_factory, tmp_path, bundle)
    apply_receipt, result = _apply(source, client_factory, tmp_path, bundle, receipt)
    assert result.ok
    assert apply_receipt.notes_created == 4
    active_roots = [
        folder
        for folder in server.folders
        if not folder.get("parent_id") and not folder.get("deleted_time")
    ]
    expected_roots = sum(1 for folder in bundle["plan"].folders if not folder.parent_node_id)
    assert len(active_roots) == expected_roots
    assert all(bundle["plan"].plan_id in folder.get("user_data", "") for folder in active_roots)
    assert len([note for note in server.created_notes if not note.get("deleted_time")]) == 4
    assert all("body" in note and "body_html" not in note for note in server.created_notes)

    second_bundle = _bundle(source, client_factory, tmp_path, suffix="same")
    assert second_bundle["plan"].plan_id == bundle["plan"].plan_id
    second_receipt, _ = _dry(source, client_factory, tmp_path, second_bundle, suffix="same")
    second_apply, second_result = _apply(
        source, client_factory, tmp_path, second_bundle, second_receipt, suffix="same"
    )
    assert second_result.completed_already
    assert second_apply.notes_created == 0


def test_replace_managed_trashes_entire_previous_export_set(export_env):
    source, server, client_factory, tmp_path = export_env
    first = _bundle(source, client_factory, tmp_path)
    first_receipt, _ = _dry(source, client_factory, tmp_path, first)
    _apply(source, client_factory, tmp_path, first, first_receipt)
    old_root_ids = {
        folder["id"]
        for folder in server.folders
        if not folder.get("parent_id") and not folder.get("deleted_time")
    }
    # Releases before the project rename used the "ojr" ownership namespace.
    # A renamed release must still recognize and replace that complete set.
    for folder in server.folders:
        user_data = folder.get("user_data")
        if not user_data:
            continue
        marker = json.loads(user_data)
        if "joplin_importer" in marker:
            marker["ojr"] = marker.pop("joplin_importer")
            folder["user_data"] = json.dumps(marker)
    for resource in server.created_resources:
        resource["title"] = str(resource.get("title", "")).replace(
            "joplin-importer:res:", "ojr:res:"
        )
    old_marker = json.loads(
        next(
            folder["user_data"]
            for folder in server.folders
            if folder["id"] in old_root_ids
        )
    )
    extra_marker = json.loads(json.dumps(old_marker))
    extra_marker["ojr"]["node_id"] = "removed-notebook"
    server.folders.append(
        {
            "id": "old-removed-root",
            "parent_id": "",
            "title": "Notebook removed from new backup",
            "deleted_time": 0,
            "user_data": json.dumps(extra_marker),
        }
    )
    old_root_ids.add("old-removed-root")

    replacement = _bundle(
        source, client_factory, tmp_path, policy="replace-managed", suffix="replace"
    )
    replacement_receipt, dry_result = _dry(
        source, client_factory, tmp_path, replacement, suffix="replace"
    )
    assert replacement_receipt.result == "ok", dry_result.problems
    apply_receipt, _ = _apply(
        source,
        client_factory,
        tmp_path,
        replacement,
        replacement_receipt,
        suffix="replace",
    )
    assert apply_receipt.old_roots_trashed == len(old_root_ids)
    assert all(
        folder.get("deleted_time")
        for folder in server.folders
        if folder["id"] in old_root_ids
    )

    writer = SnapshotWriter(tmp_path / "target-after-replacement")
    scan_joplin(
        client_factory(),
        writer,
        tool_version="0.1.0-test",
        snapshot_id="target-after-replacement",
        normalizer=Normalizer(),
    )
    target = SnapshotReader(tmp_path / "target-after-replacement")
    report = validate_export(source, target, replacement["plan"], strict_profile=True)
    assert report.result == "ok", [issue.model_dump() for issue in report.issues]
    assert target.manifest.record_counts["trashed_notes"] == len(first["plan"].notes)


def test_post_export_snapshot_validates_content_and_resources(export_env):
    source, _server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    receipt, _ = _dry(source, client_factory, tmp_path, bundle)
    _apply(source, client_factory, tmp_path, bundle, receipt)

    writer = SnapshotWriter(tmp_path / "target-after-export")
    scan_joplin(
        client_factory(),
        writer,
        tool_version="0.1.0-test",
        snapshot_id="target-after-export",
        normalizer=Normalizer(),
    )
    target = SnapshotReader(tmp_path / "target-after-export")
    report = validate_export(source, target, bundle["plan"], strict_profile=True)
    assert report.result == "ok", [issue.model_dump() for issue in report.issues]
    assert report.validated_notes == len(bundle["plan"].notes)


def test_empty_profile_can_explicitly_apply_without_impossible_jex(export_env):
    source, _server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    receipt, _ = _dry(source, client_factory, tmp_path, bundle)
    apply_receipt, result = apply_export(
        client_factory(TransportMode.WRITE_ENABLED),
        source,
        bundle["plan"],
        bundle["bodies"],
        bundle["approval"],
        receipt,
        plan_sha256=bundle["plan_sha"],
        approval_sha256=bundle["approval_sha"],
        receipt_sha256="e" * 64,
        output_dir=tmp_path / "apply-empty-no-backup",
        jex_backup_path=None,
        confirm_sync_complete=True,
        confirm_full_replace=False,
        confirm_empty_profile_no_backup=True,
    )
    assert result.ok
    assert apply_receipt.operator_confirmed_empty_profile_no_backup
    assert apply_receipt.jex_backup_path == ""


def test_empty_profile_waiver_rechecks_orphan_resources_live(export_env):
    source, server, client_factory, tmp_path = export_env
    bundle = _bundle(source, client_factory, tmp_path)
    receipt, _ = _dry(source, client_factory, tmp_path, bundle)
    server.created_resources.append({"id": "f" * 32, "title": "unexpected"})
    with pytest.raises(ExportExecutionError, match="live Joplin has"):
        apply_export(
            client_factory(TransportMode.WRITE_ENABLED),
            source,
            bundle["plan"],
            bundle["bodies"],
            bundle["approval"],
            receipt,
            plan_sha256=bundle["plan_sha"],
            approval_sha256=bundle["approval_sha"],
            receipt_sha256="e" * 64,
            output_dir=tmp_path / "must-not-apply",
            jex_backup_path=None,
            confirm_sync_complete=True,
            confirm_full_replace=False,
            confirm_empty_profile_no_backup=True,
        )
    assert server.mutating_requests() == []


def test_managed_profile_can_explicitly_replace_without_jex(export_env):
    source, _server, client_factory, tmp_path = export_env
    first = _bundle(source, client_factory, tmp_path)
    first_receipt, _ = _dry(source, client_factory, tmp_path, first)
    _apply(source, client_factory, tmp_path, first, first_receipt)

    replacement = _bundle(
        source, client_factory, tmp_path, policy="replace-managed", suffix="managed-no-jex"
    )
    receipt, _ = _dry(
        source, client_factory, tmp_path, replacement, suffix="managed-no-jex"
    )
    apply_receipt, result = apply_export(
        client_factory(TransportMode.WRITE_ENABLED),
        source,
        replacement["plan"],
        replacement["bodies"],
        replacement["approval"],
        receipt,
        plan_sha256=replacement["plan_sha"],
        approval_sha256=replacement["approval_sha"],
        receipt_sha256="d" * 64,
        output_dir=tmp_path / "apply-managed-no-jex",
        jex_backup_path=None,
        confirm_sync_complete=True,
        confirm_full_replace=True,
        confirm_managed_profile_no_backup=True,
    )
    assert result.ok
    assert apply_receipt.operator_confirmed_managed_profile_no_backup
    assert apply_receipt.old_roots_trashed > 0


def test_managed_profile_waiver_rejects_unmanaged_active_object(export_env):
    source, server, client_factory, tmp_path = export_env
    first = _bundle(source, client_factory, tmp_path)
    first_receipt, _ = _dry(source, client_factory, tmp_path, first)
    _apply(source, client_factory, tmp_path, first, first_receipt)
    replacement = _bundle(
        source, client_factory, tmp_path, policy="replace-managed", suffix="reject-unmanaged"
    )
    receipt, _ = _dry(
        source, client_factory, tmp_path, replacement, suffix="reject-unmanaged"
    )
    server.created_resources.append({"id": "a" * 32, "title": "unmanaged resource"})
    writes_before = len(server.mutating_requests())
    with pytest.raises(
        ExportExecutionError, match="resource.*not owned by joplin-importer"
    ):
        apply_export(
            client_factory(TransportMode.WRITE_ENABLED),
            source,
            replacement["plan"],
            replacement["bodies"],
            replacement["approval"],
            receipt,
            plan_sha256=replacement["plan_sha"],
            approval_sha256=replacement["approval_sha"],
            receipt_sha256="c" * 64,
            output_dir=tmp_path / "reject-unmanaged",
            jex_backup_path=None,
            confirm_sync_complete=True,
            confirm_full_replace=True,
            confirm_managed_profile_no_backup=True,
        )
    assert len(server.mutating_requests()) == writes_before
