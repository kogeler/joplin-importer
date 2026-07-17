"""Public CLI for read-only analysis and deterministic full export."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .. import __version__
from ..adapters.joplin.client import JoplinClient
from ..adapters.joplin.scanner import scan_joplin
from ..exporting.executor import (
    ExportExecutionError,
    apply_export,
    dry_run_export,
    export_instance_fingerprint,
)
from ..exporting.models import ExportDryRunReceipt
from ..exporting.planner import (
    build_export_approval,
    build_export_plan,
    load_export_approval,
    load_export_body,
    load_export_plan,
    write_export_plan,
)
from ..exporting.validation import validate_export
from ..matching.audit import run_audit
from ..models import SnapshotReader, SnapshotWriter
from ..models.hashing import sha256_file
from ..models.timeutil import now_utc_iso
from ..normalization import Normalizer
from ..reporting.csv_reports import write_csv_reports, write_extractor_errors_csv
from ..reporting.html_report import write_html_report, write_json_report
from ..secretstore import SecretError, load_token
from ..transport import HttpTransport, TransportMode

DEFAULT_BASE_URL = "http://127.0.0.1:41184"

EXIT_OK = 0
EXIT_FINDINGS = 1  # audit found problems / dry-run unsafe
EXIT_USAGE = 2
EXIT_ERROR = 3


def _echo(message: str, *, quiet: bool = False, use_json: bool = False) -> None:
    if not quiet and not use_json:
        click.echo(message)


def _json_out(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _fail(message: str, code: int = EXIT_ERROR) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(code)


def _make_client(
    base_url: str, token_file: str | None, token_env: str | None, *, write: bool = False
) -> JoplinClient:
    try:
        token = load_token(
            token_file=Path(token_file) if token_file else None,
            token_env=token_env,
        )
    except SecretError as exc:
        _fail(str(exc), EXIT_USAGE)
    mode = TransportMode.WRITE_ENABLED if write else TransportMode.READ_ONLY
    return JoplinClient(HttpTransport(base_url, mode=mode, token=token))


def _token_options(fn):
    fn = click.option("--token-file", type=click.Path(),
                      help="File containing the Joplin API token")(fn)
    fn = click.option("--token-env", help="Environment variable holding the Joplin API token")(fn)
    fn = click.option("--base-url", default=DEFAULT_BASE_URL, show_default=True)(fn)
    return fn


def _common_options(fn):
    fn = click.option("--json", "as_json", is_flag=True, help="Machine-readable output")(fn)
    fn = click.option("--verbose", is_flag=True)(fn)
    fn = click.option("--quiet", is_flag=True)(fn)
    return fn


@click.group()
@click.version_option(version=__version__, prog_name="joplin-importer")
def main() -> None:
    """Analyze OneNote/Joplin snapshots or export a complete snapshot safely."""


# -- doctor ---------------------------------------------------------------------


@main.command()
@_token_options
@_common_options
@click.option("--read-only", is_flag=True, default=True, help="Always true; doctor never writes")
def doctor(token_file, token_env, base_url, as_json, verbose, quiet, read_only) -> None:
    """Check connectivity and capabilities of the local Joplin Data API."""
    client = _make_client(base_url, token_file, token_env)
    try:
        client.ping()
        capabilities = client.probe_capabilities()
        export_fingerprint = export_instance_fingerprint(client)
        # Kept as a response-field alias for callers of older doctor versions.
        fingerprint = export_fingerprint
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc))
    payload = {
        "ping": "ok",
        "capabilities": capabilities,
        "instance_fingerprint": fingerprint,
        "export_instance_fingerprint": export_fingerprint,
        "mutating_requests_sent": len(client.transport.mutating_requests_sent()),
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(f"Joplin Data API reachable at {base_url}", quiet=quiet)
        _echo(f"instance fingerprint: {fingerprint}", quiet=quiet)
        for key, value in capabilities.items():
            _echo(f"capability {key}: {value}", quiet=quiet)


# -- scans ------------------------------------------------------------------------


@main.command("scan-onenote")
@click.option(
    "--backend",
    type=click.Choice(["com", "graph", "backup"]),
    required=True,
    help="Source backend; graph is experimental, analysis-only, and not live-validated",
)
@click.option("--output", required=True, type=click.Path())
@click.option("--notebook", help="Only scan this notebook title")
@click.option("--include-recycle-bin", is_flag=True)
@click.option("--resume", is_flag=True, help="Resume an interrupted scan in staging")
@click.option("--client-id", help="Azure app client ID (graph backend)")
@click.option(
    "--backup-root",
    type=click.Path(file_okay=False, path_type=Path),
    help="OneNote backup directory (backup backend; auto-discovered when omitted)",
)
@click.option(
    "--quarantine-file",
    "--skip-page-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSON page quarantine for the COM backend",
)
@_common_options
def scan_onenote(
    backend,
    output,
    notebook,
    include_recycle_bin,
    resume,
    client_id,
    backup_root,
    quarantine_file,
    as_json,
    verbose,
    quiet,
) -> None:
    """Inventory OneNote (Graph is experimental analysis-only)."""
    quarantine = None
    if quarantine_file is not None:
        if backend != "com":
            _fail("--quarantine-file is only valid with --backend com", EXIT_USAGE)
        from ..adapters.onenote_com.quarantine import QuarantineError, load_quarantine

        try:
            quarantine = load_quarantine(quarantine_file)
        except QuarantineError as exc:
            _fail(str(exc), EXIT_USAGE)

    if backup_root is not None and backend != "backup":
        _fail("--backup-root is only valid with --backend backup", EXIT_USAGE)
    if backend == "graph" and not client_id:
        _fail("--client-id is required for the graph backend", EXIT_USAGE)

    backup_inventory = None
    resolved_backup_root = None
    if backend == "backup":
        from ..adapters.onenote_backup.discovery import (
            BackupDiscoveryError,
            discover_backup_root,
            discover_latest_sections,
        )

        try:
            resolved_backup_root = discover_backup_root(backup_root)
            backup_inventory = discover_latest_sections(
                resolved_backup_root,
                include_recycle_bin=include_recycle_bin,
                notebook_filter=notebook,
            )
        except BackupDiscoveryError as exc:
            _fail(str(exc), EXIT_USAGE)

    output_path = Path(output)
    snapshot_id = f"{now_utc_iso().replace(':', '')}-onenote-{backend}"
    writer = SnapshotWriter(output_path, resume=resume)
    progress = (lambda msg: click.echo(msg, err=True)) if verbose else None

    if backend == "com":
        from ..adapters.onenote_com.api import ComOneNoteApi, OneNoteApiError
        from ..adapters.onenote_com.scanner import scan_onenote_com

        try:
            api = ComOneNoteApi()
        except OneNoteApiError as exc:
            _fail(str(exc))
        manifest = scan_onenote_com(
            api,
            writer,
            tool_version=__version__,
            snapshot_id=snapshot_id,
            include_recycle_bin=include_recycle_bin,
            notebook_filter=notebook,
            quarantine=quarantine,
            normalizer=Normalizer(),
            on_progress=progress,
        )
    elif backend == "graph":
        from ..adapters.onenote_graph.scanner import scan_onenote_graph

        manifest = scan_onenote_graph(
            client_id,
            writer,
            tool_version=__version__,
            snapshot_id=snapshot_id,
            notebook_filter=notebook,
            normalizer=Normalizer(),
            on_progress=progress,
        )
    else:
        from ..adapters.onenote_backup.scanner import scan_onenote_backup

        assert resolved_backup_root is not None
        assert backup_inventory is not None
        manifest = scan_onenote_backup(
            resolved_backup_root,
            writer,
            tool_version=__version__,
            snapshot_id=snapshot_id,
            root_mode="manual" if backup_root is not None else "auto",
            include_recycle_bin=include_recycle_bin,
            notebook_filter=notebook,
            normalizer=Normalizer(),
            on_progress=progress,
            inventory=backup_inventory,
        )

    payload = {
        "snapshot": str(output_path),
        "records": manifest.record_counts,
        "coverage": manifest.coverage_status,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(f"snapshot written to {output_path} ({manifest.coverage_status})", quiet=quiet)
    sys.exit(EXIT_OK if manifest.coverage_status == "complete" else EXIT_FINDINGS)


@main.command("scan-joplin")
@_token_options
@click.option("--output", required=True, type=click.Path())
@click.option("--no-resources", is_flag=True, help="Skip downloading resource files")
@click.option("--resume", is_flag=True)
@_common_options
def scan_joplin_cmd(token_file, token_env, base_url, output, no_resources, resume,
                    as_json, verbose, quiet) -> None:
    """Inventory the Joplin profile through the local Data API (read-only)."""
    client = _make_client(base_url, token_file, token_env)
    output_path = Path(output)
    writer = SnapshotWriter(output_path, resume=resume)
    manifest = scan_joplin(
        client,
        writer,
        tool_version=__version__,
        snapshot_id=f"{now_utc_iso().replace(':', '')}-joplin",
        download_resources=not no_resources,
        normalizer=Normalizer(),
        on_progress=(lambda msg: click.echo(msg, err=True)) if verbose else None,
    )
    assert client.transport.mutating_requests_sent() == []
    payload = {
        "snapshot": str(output_path),
        "records": manifest.record_counts,
        "coverage": manifest.coverage_status,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(f"snapshot written to {output_path} ({manifest.coverage_status})", quiet=quiet)
    sys.exit(EXIT_OK if manifest.coverage_status == "complete" else EXIT_FINDINGS)


# -- compare / plan / approve ---------------------------------------------------------


@main.command()
@click.argument("source_snapshot", type=click.Path(exists=True))
@click.argument("target_snapshot", type=click.Path(exists=True))
@click.option("--additional-source", multiple=True, type=click.Path(exists=True),
              help="Corroborating source snapshot (recorded in the report metadata)")
@click.option("--output", required=True, type=click.Path())
@_common_options
def compare(source_snapshot, target_snapshot, additional_source, output,
            as_json, verbose, quiet) -> None:
    """Compare a OneNote snapshot with a Joplin snapshot and write reports."""
    source = SnapshotReader(Path(source_snapshot))
    target = SnapshotReader(Path(target_snapshot))
    result = run_audit(source, target, tool_version=__version__)

    output_dir = Path(output)
    write_json_report(result, output_dir)
    write_html_report(result, output_dir)
    write_csv_reports(result, output_dir)
    errors = []
    for reader, label in [(source, "source"), (target, "target")]:
        for record in reader.iter_errors():
            errors.append({"snapshot": label, **record.canonical_dict()})
    write_extractor_errors_csv(errors, output_dir)

    problem_kinds = {
        k: v
        for k, v in result.summary.findings_by_kind.items()
        if k not in ("representation-only",)
    }
    payload = {
        "reports": str(output_dir),
        "source_pages": result.summary.source_pages,
        "target_notes": result.summary.target_notes,
        "matches_by_confidence": result.summary.matches_by_confidence,
        "findings_by_kind": result.summary.findings_by_kind,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(f"audit reports written to {output_dir}", quiet=quiet)
        for kind, count in sorted(result.summary.findings_by_kind.items()):
            _echo(f"  {kind}: {count}", quiet=quiet)
    sys.exit(EXIT_FINDINGS if problem_kinds else EXIT_OK)


# -- deterministic full export ----------------------------------------------------


@main.command("export-plan")
@click.option("--source-snapshot", required=True, type=click.Path(exists=True))
@click.option(
    "--on-conflict",
    "conflict_policy",
    default="fail",
    show_default=True,
    type=click.Choice(["fail", "replace-managed"]),
)
@click.option("--target-fingerprint", default="")
@click.option("--output", required=True, type=click.Path())
@_common_options
def export_plan_cmd(source_snapshot, conflict_policy, target_fingerprint, output,
                    as_json, verbose, quiet) -> None:
    """Plan a complete export of every source page without merging."""
    source = SnapshotReader(Path(source_snapshot))
    try:
        plan, bodies = build_export_plan(
            source,
            tool_version=__version__,
            conflict_policy=conflict_policy,
            target_instance_fingerprint=target_fingerprint,
        )
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)
    path = write_export_plan(plan, bodies, Path(output))
    payload = {
        "plan": str(path),
        "plan_id": plan.plan_id,
        "plan_sha256": sha256_file(path),
        "folders": len(plan.folders),
        "notes": len(plan.notes),
        "conflict_policy": plan.conflict_policy,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(
            f"full export plan: {len(plan.folders)} folders, {len(plan.notes)} notes -> {path}",
            quiet=quiet,
        )


@main.command("export-approve")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--operator", default="")
@click.option("--output", required=True, type=click.Path())
@_common_options
def export_approve_cmd(plan_file, operator, output, as_json, verbose, quiet) -> None:
    """Approve one complete export plan; partial export is intentionally unsupported."""
    try:
        approval = build_export_approval(Path(plan_file), operator=operator)
    except (ValueError, OSError) as exc:
        _fail(str(exc), EXIT_USAGE)
    Path(output).write_text(approval.model_dump_json(indent=2), encoding="utf-8")
    payload = {"approval": output, "plan_sha256": approval.export_plan_sha256}
    if as_json:
        _json_out(payload)
    else:
        _echo(f"approved complete export -> {output}", quiet=quiet)


def _load_export_bundle(plan_file: str, approval_file: str):
    plan_path = Path(plan_file)
    plan, plan_sha = load_export_plan(plan_path)
    approval, approval_sha = load_export_approval(Path(approval_file))
    if approval.export_plan_sha256 != plan_sha:
        _fail("approval file was generated for a different export plan", EXIT_USAGE)
    bodies = {note.action_id: load_export_body(plan_path, note.action_id) for note in plan.notes}
    return plan, plan_sha, approval, approval_sha, bodies


def _run_export_dry_run(plan_file, approval_file, source_snapshot, base_url, token_file,
                        token_env, output, as_json, quiet) -> None:
    plan, plan_sha, approval, approval_sha, bodies = _load_export_bundle(
        plan_file, approval_file
    )
    source = SnapshotReader(Path(source_snapshot))
    client = _make_client(base_url, token_file, token_env)
    try:
        receipt, result = dry_run_export(
            client,
            source,
            plan,
            bodies,
            approval,
            plan_sha256=plan_sha,
            approval_sha256=approval_sha,
            output_dir=Path(output),
        )
    except ExportExecutionError as exc:
        _fail(str(exc))
    payload = {
        "result": receipt.result,
        "operations": len(result.operations),
        "folders": len(plan.folders),
        "notes": len(plan.notes),
        "mutating_requests_sent": receipt.mutating_requests_sent,
        "problems": result.problems,
        "output": output,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(
            f"export dry-run {receipt.result}: {len(plan.folders)} folders, "
            f"{len(plan.notes)} notes",
            quiet=quiet,
        )
        _echo(f"mutating requests sent: {receipt.mutating_requests_sent}", quiet=quiet)
        for problem in result.problems:
            click.echo(f"problem: {problem}", err=True)
    if receipt.result != "ok":
        raise click.exceptions.Exit(EXIT_FINDINGS)


@main.command("export-dry-run")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--approval-file", required=True, type=click.Path(exists=True))
@click.option("--source-snapshot", required=True, type=click.Path(exists=True))
@_token_options
@click.option("--output", required=True, type=click.Path())
@_common_options
def export_dry_run_cmd(plan_file, approval_file, source_snapshot, token_file, token_env,
                       base_url, output, as_json, verbose, quiet) -> None:
    """Simulate a complete managed export using read-only Joplin traffic."""
    _run_export_dry_run(
        plan_file,
        approval_file,
        source_snapshot,
        base_url,
        token_file,
        token_env,
        output,
        as_json,
        quiet,
    )


@main.command("export-apply")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--approval-file", required=True, type=click.Path(exists=True))
@click.option("--source-snapshot", required=True, type=click.Path(exists=True))
@click.option("--dry-run-receipt", "receipt_file", type=click.Path(exists=True))
@click.option("--dry-run", "simulate", is_flag=True)
@click.option("--jex-backup", type=click.Path(exists=True))
@click.option("--confirm-sync-complete", is_flag=True)
@click.option("--confirm-full-replace", is_flag=True)
@click.option(
    "--confirm-empty-profile-no-backup",
    is_flag=True,
    help="Allow no JEX only when live Joplin is proven completely empty",
)
@click.option(
    "--confirm-managed-profile-no-backup",
    is_flag=True,
    help="Allow no JEX only when every active object is proven importer-managed",
)
@_token_options
@click.option("--output", required=True, type=click.Path())
@_common_options
def export_apply_cmd(plan_file, approval_file, source_snapshot, receipt_file, simulate,
                     jex_backup, confirm_sync_complete, confirm_full_replace,
                     confirm_empty_profile_no_backup, confirm_managed_profile_no_backup,
                     token_file, token_env, base_url, output, as_json, verbose, quiet) -> None:
    """Apply a complete export after its successful dry-run."""
    if simulate:
        _run_export_dry_run(
            plan_file,
            approval_file,
            source_snapshot,
            base_url,
            token_file,
            token_env,
            output,
            as_json,
            quiet,
        )
        return
    if not receipt_file:
        _fail(
            "--dry-run-receipt is required; run 'joplin-importer export-dry-run' first",
            EXIT_USAGE,
        )
    plan, plan_sha, approval, approval_sha, bodies = _load_export_bundle(
        plan_file, approval_file
    )
    receipt_path = Path(receipt_file)
    receipt = ExportDryRunReceipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    source = SnapshotReader(Path(source_snapshot))
    client = _make_client(base_url, token_file, token_env, write=True)
    root_count = sum(1 for folder in plan.folders if not folder.parent_node_id)
    click.echo(
        f"about to export {len(plan.notes)} notes from {root_count} "
        f"OneNote notebooks (plan {plan.plan_id})",
        err=True,
    )
    try:
        apply_receipt, result = apply_export(
            client,
            source,
            plan,
            bodies,
            approval,
            receipt,
            plan_sha256=plan_sha,
            approval_sha256=approval_sha,
            receipt_sha256=sha256_file(receipt_path),
            output_dir=Path(output),
            jex_backup_path=Path(jex_backup) if jex_backup else None,
            confirm_sync_complete=confirm_sync_complete,
            confirm_full_replace=confirm_full_replace,
            confirm_empty_profile_no_backup=confirm_empty_profile_no_backup,
            confirm_managed_profile_no_backup=confirm_managed_profile_no_backup,
        )
    except ExportExecutionError as exc:
        _fail(str(exc))
    payload = {
        "folders_created": apply_receipt.folders_created,
        "notes_created": apply_receipt.notes_created,
        "resources_created": apply_receipt.resources_created,
        "old_roots_trashed": apply_receipt.old_roots_trashed,
        "completed_already": result.completed_already,
        "output": output,
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(
            f"full export completed: {apply_receipt.notes_created} notes created, "
            f"{apply_receipt.old_roots_trashed} old roots trashed",
            quiet=quiet,
        )


@main.command("export-validate")
@click.argument("plan_file", type=click.Path(exists=True))
@click.option("--source-snapshot", required=True, type=click.Path(exists=True))
@click.option("--target-snapshot", required=True, type=click.Path(exists=True))
@click.option(
    "--strict-profile",
    is_flag=True,
    help="Also require total Joplin folder/note counts to equal the export plan",
)
@click.option("--output", required=True, type=click.Path())
@_common_options
def export_validate_cmd(plan_file, source_snapshot, target_snapshot, strict_profile,
                        output, as_json, verbose, quiet) -> None:
    """Validate a post-export Joplin snapshot by embedded OneNote page IDs."""
    plan, _digest = load_export_plan(Path(plan_file))
    report = validate_export(
        SnapshotReader(Path(source_snapshot)),
        SnapshotReader(Path(target_snapshot)),
        plan,
        strict_profile=strict_profile,
    )
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    by_kind: dict[str, int] = {}
    for issue in report.issues:
        by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1
    payload = {
        "result": report.result,
        "validated_notes": report.validated_notes,
        "planned_notes": report.planned_notes,
        "target_notes": report.target_notes,
        "issues": by_kind,
        "output": str(output_path),
    }
    if as_json:
        _json_out(payload)
    else:
        _echo(
            f"export validation {report.result}: {report.validated_notes}/"
            f"{report.planned_notes} notes validated",
            quiet=quiet,
        )
        for kind, count in sorted(by_kind.items()):
            _echo(f"  {kind}: {count}", quiet=quiet)
    if report.result != "ok":
        raise click.exceptions.Exit(EXIT_FINDINGS)


if __name__ == "__main__":
    main()
