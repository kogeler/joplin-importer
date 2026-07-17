"""Shared action compiler + dry-run/apply executor.

One code path compiles every operation for both modes:

* dry-run runs with a READ_ONLY transport — mutating operations are predicted
  (order, method, endpoint, payload hash) but never sent; the transport ledger
  proves it.
* apply runs the same compiler with a WRITE_ENABLED transport and actually
  executes, revalidating live preconditions and keeping an append-only journal.

The historical dry-run entry point calls exactly :func:`dry_run`; there is no second
simulation path that can drift from the real executor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.joplin.client import JoplinClient
from ..models import SnapshotReader
from ..models.hashing import sha256_canonical_json, sha256_text
from ..models.timeutil import epoch_seconds, now_utc_iso
from ..transport import LedgerEntry, TransportMode
from .content import RESOURCE_PLACEHOLDER
from .models import (
    ApplyReceipt,
    ApprovalFile,
    DryRunReceipt,
    JournalEntry,
    RepairAction,
    RepairPlan,
)

_NOTE_SEARCH_FIELDS = ["id", "title", "parent_id"]


class RepairExecutionError(RuntimeError):
    pass


@dataclass
class Operation:
    order: int
    action_id: str
    # resolve-destination | idempotency-check | create-folder | create-resource
    # | create-note | verify
    kind: str
    method: str
    endpoint: str
    payload_sha256: str = ""
    mutating: bool = False
    result: str = ""  # predicted | applied | skipped | failed | ok
    reason: str = ""
    joplin_id: str = ""

    def to_dict(self) -> dict:
        return {
            "order": self.order,
            "action_id": self.action_id,
            "kind": self.kind,
            "method": self.method,
            "endpoint": self.endpoint,
            "payload_sha256": self.payload_sha256,
            "mutating": self.mutating,
            "result": self.result,
            "reason": self.reason,
            "joplin_id": self.joplin_id,
        }


@dataclass
class ExecutionResult:
    operations: list[Operation] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    observations: list[str] = field(default_factory=list)  # live precondition inputs
    conversion_notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.failed == 0

    def precondition_fingerprint(self) -> str:
        return sha256_canonical_json(sorted(self.observations))


def compute_instance_fingerprint(
    client: JoplinClient, *, exclude_titles: frozenset[str] | None = None
) -> str:
    """Stable identity of a Joplin profile: hash of its folder IDs.

    Recovery notebooks created by this tool are excluded so that the tool's own
    activity never invalidates the fingerprint.
    """
    from .models import RECOVERY_NOTEBOOK

    excluded = exclude_titles if exclude_titles is not None else frozenset({RECOVERY_NOTEBOOK})
    folder_ids = sorted(
        f["id"]
        for f in client.iter_folders()
        if f.get("title") not in excluded and not f.get("deleted_time")
    )
    return sha256_canonical_json(folder_ids)[:16]


# -- core compiler/executor ---------------------------------------------------------


class _Runner:
    def __init__(
        self,
        client: JoplinClient,
        source: SnapshotReader,
        plan: RepairPlan,
        bodies: dict[str, str],
        selected: list[str],
        *,
        execute: bool,
        journal: list[JournalEntry] | None = None,
    ) -> None:
        self.client = client
        self.source = source
        self.plan = plan
        self.bodies = bodies
        self.selected = selected
        self.execute = execute
        self.journal = journal
        self.result = ExecutionResult()
        self._order = 0
        self._resource_paths = self._index_resources(source)

    # -- driving -----------------------------------------------------------------

    def run(self) -> ExecutionResult:
        fingerprint = compute_instance_fingerprint(
            self.client, exclude_titles=frozenset({self.plan.destination_notebook})
        )
        self.result.observations.append(f"instance:{fingerprint}")
        if self.plan.target_instance_fingerprint not in ("", fingerprint):
            self.result.problems.append(
                "target instance fingerprint mismatch: plan was computed against "
                f"{self.plan.target_instance_fingerprint}, live instance is {fingerprint}"
            )
            return self.result

        destination_id = self._resolve_destination()
        actions = {a.action_id: a for a in self.plan.actions}
        for action_id in self.selected:
            action = actions.get(action_id)
            if action is None:
                self.result.problems.append(f"selected action not in plan: {action_id}")
                continue
            try:
                self._run_action(action, destination_id)
            except RepairExecutionError as exc:
                self.result.failed += 1
                self.result.problems.append(f"action {action_id}: {exc}")
                self._journal(action, "action", "failed", detail=str(exc))
        return self.result

    # -- steps ----------------------------------------------------------------------

    def _resolve_destination(self) -> str | None:
        title = self.plan.destination_notebook
        matches = [
            f
            for f in self.client.iter_folders()
            if f.get("title") == title and not f.get("deleted_time")
        ]
        op = self._op("", "resolve-destination", "GET", "/folders")
        if len(matches) > 1:
            op.result = "failed"
            op.reason = f"{len(matches)} notebooks named {title!r}"
            self.result.problems.append(
                f"destination notebook name {title!r} is ambiguous ({len(matches)} folders)"
            )
            return None
        if len(matches) == 1:
            op.result = "ok"
            op.joplin_id = matches[0]["id"]
            self.result.observations.append(f"destination:{matches[0]['id']}")
            return matches[0]["id"]
        op.result = "ok"
        op.reason = "destination notebook missing; will be created"
        self.result.observations.append("destination:absent")
        create = self._op(
            "",
            "create-folder",
            "POST",
            "/folders",
            payload={"title": title},
            mutating=True,
        )
        if not self.execute:
            create.result = "predicted"
            return None  # unknown until apply
        response = self.client.transport.request(
            "POST", "/folders", json_body={"title": title}
        )
        folder_id = response.json()["id"]
        create.result = "applied"
        create.joplin_id = folder_id
        return folder_id

    def _run_action(self, action: RepairAction, destination_id: str | None) -> None:
        if str(action.action) != "create-recovery-copy":
            raise RepairExecutionError(
                f"unsupported action type {action.action}; only create-recovery-copy "
                "is enabled in this version"
            )
        body = self.bodies.get(action.action_id)
        if body is None:
            raise RepairExecutionError("recovery body is missing from the plan bundle")
        if sha256_text(body) != action.expected_body_sha256:
            raise RepairExecutionError(
                "recovery body hash does not match the plan (bundle was modified?)"
            )

        # idempotency: a note carrying this action marker must not exist yet
        existing = self._find_by_marker(action)
        if existing is not None:
            op = self._op(action.action_id, "idempotency-check", "GET", "/search")
            op.result = "skipped"
            op.reason = f"note {existing} already carries idempotency key"
            op.joplin_id = existing
            self.result.skipped += 1
            self.result.observations.append(f"{action.action_id}:exists:{existing}")
            self._journal(action, "idempotency-check", "skipped", joplin_id=existing)
            return
        op = self._op(action.action_id, "idempotency-check", "GET", "/search")
        op.result = "ok"
        op.reason = "no existing recovery note"
        self.result.observations.append(f"{action.action_id}:absent")

        # resources first (so an interruption before note creation stays resumable)
        resource_ids: dict[str, str] = {}
        for digest in action.expected_resource_hashes:
            resource_ids[digest] = self._ensure_resource(action, digest)

        final_body = body
        for digest, joplin_id in resource_ids.items():
            final_body = final_body.replace(
                RESOURCE_PLACEHOLDER.format(sha256=digest), f":/{joplin_id}"
            )

        payload = {
            "title": action.expected_title,
            "destination_notebook": action.destination_notebook,
            "body_template_sha256": action.expected_body_sha256,
            "intended_format": action.intended_content_format,
            "source_page_id": action.source_page_id,
        }
        op = self._op(
            action.action_id,
            "create-note",
            "POST",
            "/notes",
            payload=payload,
            mutating=True,
        )
        if not self.execute:
            op.result = "predicted"
            return

        if destination_id is None:
            raise RepairExecutionError("destination notebook could not be resolved")
        page = self._page(action.source_page_id)
        note_payload: dict = {
            "parent_id": destination_id,
            "title": action.expected_title,
            "source_url": f"onenote://page/{_url_quote(action.source_page_id)}",
            "source_application": "joplin-importer",
        }
        if action.intended_content_format == "html":
            note_payload["body_html"] = final_body
        else:
            note_payload["body"] = final_body
        if page is not None:  # preserve original timestamps
            created_ms = _iso_to_ms(page.created_at)
            updated_ms = _iso_to_ms(page.updated_at)
            if created_ms:
                note_payload["user_created_time"] = created_ms
            if updated_ms:
                note_payload["user_updated_time"] = updated_ms
        response = self.client.transport.request("POST", "/notes", json_body=note_payload)
        note_id = response.json().get("id", "")
        op.result = "applied"
        op.joplin_id = note_id
        self.result.applied += 1
        self._journal(action, "create-note", "ok", joplin_id=note_id)
        self._verify_created(action, note_id)

    def _ensure_resource(self, action: RepairAction, digest: str) -> str:
        marker = f"joplin-importer:res:{digest[:12]}"
        existing: list[dict] = []
        for candidate in (marker, f"ojr:res:{digest[:12]}"):
            existing = [
                r
                for r in self.client.search(
                    candidate, item_type="resource", fields=["id", "title"]
                )
            ]
            if existing:
                break
        if existing:
            op = self._op(action.action_id, "idempotency-check", "GET", "/search")
            op.result = "skipped"
            op.reason = f"resource {digest[:12]} already uploaded"
            op.joplin_id = existing[0]["id"]
            return existing[0]["id"]

        data, filename = self._resource_bytes(digest)
        payload = {"sha256": digest, "title": marker}
        op = self._op(
            action.action_id,
            "create-resource",
            "POST",
            "/resources",
            payload=payload,
            mutating=True,
        )
        if not self.execute:
            op.result = "predicted"
            return f"pending{digest[:26]}"
        props = {"title": f"{filename or 'resource'} [{marker}]", "filename": filename or digest}
        response = self.client.transport.request(
            "POST",
            "/resources",
            files={
                "data": (filename or digest, data),
                "props": (None, json.dumps(props)),
            },
        )
        resource_id = response.json().get("id", "")
        op.result = "applied"
        op.joplin_id = resource_id
        self._journal(action, "create-resource", "ok", joplin_id=resource_id, detail=digest[:12])
        return resource_id

    def _verify_created(self, action: RepairAction, note_id: str) -> None:
        """Re-read the created note; conversion differences are recorded, not fatal."""
        op = self._op(action.action_id, "verify", "GET", f"/notes/{note_id}")
        try:
            note = self.client.get_note(note_id, ["id", "title", "parent_id", "body"])
        except Exception as exc:  # noqa: BLE001 - verification must not lose the journal
            op.result = "failed"
            op.reason = f"re-read failed: {exc}"
            self.result.problems.append(f"action {action.action_id}: verification re-read failed")
            return
        op.result = "ok"
        if note.get("title") != action.expected_title:
            self.result.conversion_notes.append(
                f"action {action.action_id}: created title differs from plan"
            )
        if action.intended_content_format == "html" and not note.get("body"):
            self.result.conversion_notes.append(
                f"action {action.action_id}: Joplin produced an empty body from body_html"
            )
        self._journal(action, "verify", "ok", joplin_id=note_id)

    # -- helpers ---------------------------------------------------------------------

    def _find_by_marker(self, action: RepairAction) -> str | None:
        for marker in (
            f"joplin-importer:action_id={action.action_id}",
            f"ojr:action_id={action.action_id}",
        ):
            for item in self.client.search(f'"{marker}"', fields=_NOTE_SEARCH_FIELDS):
                return item["id"]
        return None

    def _page(self, page_id: str):
        for page in self.source.iter_pages():
            if page.source_page_id == page_id:
                return page
        return None

    def _resource_bytes(self, digest: str) -> tuple[bytes, str | None]:
        entry = self._resource_paths.get(digest)
        if entry is None:
            raise RepairExecutionError(
                f"resource {digest[:12]} is not stored in the source snapshot"
            )
        stored_path, filename = entry
        return self.source.read_relative(stored_path), filename

    @staticmethod
    def _index_resources(source: SnapshotReader) -> dict[str, tuple[str, str | None]]:
        index: dict[str, tuple[str, str | None]] = {}
        for page in source.iter_pages():
            for resource in page.resources:
                if resource.sha256 and resource.stored_path:
                    index.setdefault(
                        resource.sha256,
                        (resource.stored_path, resource.original_filename),
                    )
        return index

    def _op(
        self,
        action_id: str,
        kind: str,
        method: str,
        endpoint: str,
        *,
        payload: dict | None = None,
        mutating: bool = False,
    ) -> Operation:
        self._order += 1
        op = Operation(
            order=self._order,
            action_id=action_id,
            kind=kind,
            method=method,
            endpoint=endpoint,
            payload_sha256=sha256_canonical_json(payload) if payload is not None else "",
            mutating=mutating,
        )
        self.result.operations.append(op)
        return op

    def _journal(
        self,
        action: RepairAction,
        step: str,
        status: str,
        *,
        joplin_id: str = "",
        detail: str = "",
    ) -> None:
        if self.journal is None:
            return
        self.journal.append(
            JournalEntry(
                at_utc=now_utc_iso(),
                action_id=action.action_id,
                idempotency_key=action.idempotency_key,
                step=step,
                status=status,
                joplin_id=joplin_id,
                detail=detail,
            )
        )


# -- public entry points ------------------------------------------------------------


def dry_run(
    client: JoplinClient,
    source: SnapshotReader,
    plan: RepairPlan,
    bodies: dict[str, str],
    approval: ApprovalFile,
    *,
    plan_sha256: str,
    approval_sha256: str,
    output_dir: Path,
) -> tuple[DryRunReceipt, ExecutionResult]:
    """Simulate the plan against live Joplin state without mutating it."""
    if client.transport.mode is not TransportMode.READ_ONLY:
        raise RepairExecutionError("dry-run requires a READ_ONLY transport")
    if approval.repair_plan_sha256 != plan_sha256:
        raise RepairExecutionError("approval digest does not match the repair plan file")

    client.probe_capabilities()
    runner = _Runner(
        client, source, plan, bodies, approval.approved_action_ids, execute=False
    )
    result = runner.run()

    sent = client.transport.mutating_requests_sent()
    if sent:  # defense in depth: the transport must have made this impossible
        result.problems.append(f"{len(sent)} mutating request(s) escaped the guard")

    receipt = DryRunReceipt(
        created_at_utc=now_utc_iso(),
        plan_id=plan.plan_id,
        repair_plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        selected_action_ids=list(approval.approved_action_ids),
        target_instance_fingerprint=plan.target_instance_fingerprint,
        joplin_api_version="JoplinClipperServer",
        live_precondition_fingerprint=result.precondition_fingerprint(),
        result="ok" if result.ok else "failed",
        problems=list(result.problems),
        mutating_requests_sent=len(sent),
    )
    _write_dry_run_outputs(receipt, result, client.transport.ledger, output_dir)
    return receipt, result


def apply_plan(
    client: JoplinClient,
    source: SnapshotReader,
    plan: RepairPlan,
    bodies: dict[str, str],
    approval: ApprovalFile,
    receipt: DryRunReceipt,
    *,
    plan_sha256: str,
    approval_sha256: str,
    receipt_sha256: str,
    output_dir: Path,
    jex_backup_path: Path | None,
    confirm_sync_complete: bool,
    confirm_dedicated_notebook: bool,
) -> tuple[ApplyReceipt, ExecutionResult]:
    """Apply approved actions after verifying the dry-run receipt and backups."""
    problems = _verify_apply_preflight(
        plan,
        approval,
        receipt,
        plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        jex_backup_path=jex_backup_path,
        confirm_sync_complete=confirm_sync_complete,
        confirm_dedicated_notebook=confirm_dedicated_notebook,
    )
    if problems:
        raise RepairExecutionError("; ".join(problems))
    if client.transport.mode is not TransportMode.WRITE_ENABLED:
        raise RepairExecutionError("apply requires a WRITE_ENABLED transport")

    client.probe_capabilities()
    live_fingerprint = compute_instance_fingerprint(
        client, exclude_titles=frozenset({plan.destination_notebook})
    )
    if receipt.target_instance_fingerprint not in ("", live_fingerprint):
        raise RepairExecutionError(
            "dry-run receipt was produced against a different Joplin instance"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    journal: list[JournalEntry] = []
    runner = _Runner(
        client,
        source,
        plan,
        bodies,
        approval.approved_action_ids,
        execute=True,
        journal=journal,
    )
    try:
        result = runner.run()
    finally:
        # the journal must survive even a mid-apply crash
        journal_path = output_dir / "apply-journal.jsonl"
        with journal_path.open("a", encoding="utf-8") as fh:  # append-only
            for entry in journal:
                fh.write(entry.model_dump_json() + "\n")

    apply_receipt = ApplyReceipt(
        created_at_utc=now_utc_iso(),
        plan_id=plan.plan_id,
        repair_plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        dry_run_receipt_sha256=receipt_sha256,
        jex_backup_path=str(jex_backup_path) if jex_backup_path else "",
        jex_backup_sha256=(
            sha256_text(jex_backup_path.read_bytes().hex())
            if jex_backup_path and jex_backup_path.exists()
            else ""
        ),
        operator_confirmed_sync_complete=confirm_sync_complete,
        operator_confirmed_dedicated_notebook=confirm_dedicated_notebook,
        actions_applied=result.applied,
        actions_skipped=result.skipped,
        actions_failed=result.failed,
    )
    (output_dir / "apply-receipt.json").write_text(
        apply_receipt.model_dump_json(indent=2), encoding="utf-8"
    )
    _write_operations(result, output_dir / "apply-operations.jsonl")
    return apply_receipt, result


def _verify_apply_preflight(
    plan: RepairPlan,
    approval: ApprovalFile,
    receipt: DryRunReceipt,
    *,
    plan_sha256: str,
    approval_sha256: str,
    jex_backup_path: Path | None,
    confirm_sync_complete: bool,
    confirm_dedicated_notebook: bool,
) -> list[str]:
    problems: list[str] = []
    if approval.repair_plan_sha256 != plan_sha256:
        problems.append("approval digest does not match the repair plan file")
    if receipt.repair_plan_sha256 != plan_sha256:
        problems.append("dry-run receipt refers to a different repair plan")
    if receipt.approval_sha256 != approval_sha256:
        problems.append("dry-run receipt refers to a different approval file")
    if receipt.result != "ok":
        problems.append("dry-run receipt records a failed simulation; re-run dry-run")
    if set(receipt.selected_action_ids) != set(approval.approved_action_ids):
        problems.append("dry-run receipt covers a different set of actions")
    if receipt.plan_id != plan.plan_id:
        problems.append("dry-run receipt plan_id mismatch")
    if jex_backup_path is None or not jex_backup_path.exists():
        problems.append(
            "a JEX backup path must be supplied and exist before apply "
            "(create it in Joplin: File > Export All > JEX)"
        )
    if not confirm_sync_complete:
        problems.append("operator must confirm Joplin synchronization has completed")
    if not confirm_dedicated_notebook:
        problems.append("operator must acknowledge the dedicated recovery notebook")
    return problems


def _write_dry_run_outputs(
    receipt: DryRunReceipt,
    result: ExecutionResult,
    transport_ledger: list[LedgerEntry],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "receipt": receipt.model_dump(),
        "operations": [op.to_dict() for op in result.operations],
        "problems": result.problems,
        "conversion_notes": result.conversion_notes,
    }
    (output_dir / "dry-run-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "receipt.json").write_text(
        receipt.model_dump_json(indent=2), encoding="utf-8"
    )
    _write_operations(result, output_dir / "operations.jsonl")
    _write_transport_ledger(transport_ledger, output_dir / "transport-ledger.jsonl")
    _write_dry_run_html(receipt, result, output_dir / "dry-run-summary.html")


def _write_operations(result: ExecutionResult, path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for op in result.operations:
            fh.write(json.dumps(op.to_dict(), ensure_ascii=False) + "\n")


def _write_transport_ledger(entries: list[LedgerEntry], path: Path) -> None:
    """Persist the redacted ledger of requests that actually reached transport."""
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(
                json.dumps(
                    {
                        "method": entry.method,
                        "url": entry.url,
                        "status_code": entry.status_code,
                        "error": entry.error,
                        "attempts": entry.attempts,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _write_dry_run_html(
    receipt: DryRunReceipt, result: ExecutionResult, path: Path
) -> None:
    import html as _html

    rows = "".join(
        "<tr>"
        f"<td>{op.order}</td><td>{_html.escape(op.action_id)}</td>"
        f"<td>{_html.escape(op.kind)}</td><td>{op.method}</td>"
        f"<td>{_html.escape(op.endpoint)}</td><td>{_html.escape(op.result)}</td>"
        f"<td>{_html.escape(op.reason)}</td>"
        "</tr>"
        for op in result.operations
    )
    problems = "".join(f"<li>{_html.escape(p)}</li>" for p in result.problems)
    path.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Dry-run summary</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:1.5rem}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:3px 8px;"
        "font-size:.85rem}</style></head><body>"
        f"<h1>Dry-run: {'OK' if receipt.result == 'ok' else 'FAILED'}</h1>"
        f"<p>Plan {_html.escape(receipt.plan_id)} · "
        f"{len(receipt.selected_action_ids)} action(s) · "
        f"mutating requests sent: {receipt.mutating_requests_sent}</p>"
        + (f"<h2>Problems</h2><ul>{problems}</ul>" if problems else "")
        + "<h2>Operations</h2><table><tr><th>#</th><th>Action</th><th>Kind</th>"
        "<th>Method</th><th>Endpoint</th><th>Result</th><th>Reason</th></tr>"
        + rows
        + "</table></body></html>",
        encoding="utf-8",
    )


def _iso_to_ms(iso_utc: str | None) -> int | None:
    seconds = epoch_seconds(iso_utc)
    return int(seconds * 1000) if seconds is not None else None


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
