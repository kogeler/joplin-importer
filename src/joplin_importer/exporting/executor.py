"""Dry-run and apply executor for a complete, managed OneNote export.

The workflow never merges notes.  It stages the entire new export under
temporary top-level names, verifies every planned folder and note, promotes
the new roots, and only then moves the previous managed export set to
Joplin's trash.  Unmanaged Joplin data is never updated or deleted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from ..adapters.joplin.client import JoplinClient
from ..models import SnapshotReader
from ..models.hashing import sha256_canonical_json, sha256_file, sha256_text
from ..models.timeutil import epoch_seconds, now_utc_iso
from ..transport import LedgerEntry, TransportMode
from .content import RESOURCE_PLACEHOLDER
from .models import (
    ExportApplyReceipt,
    ExportApproval,
    ExportDryRunReceipt,
    ExportFolder,
    ExportNote,
    ExportPlan,
)

_MARKER_NAMESPACE = "joplin_importer"
_LEGACY_MARKER_NAMESPACE = "ojr"
_ROOT_KIND = "onenote-export-root"
_FOLDER_KIND = "onenote-export-folder"
_RESOURCE_MARKER_PREFIX = "joplin-importer:res:"
_LEGACY_RESOURCE_MARKER_PREFIX = "ojr:res:"
_RESOURCE_MARKER_RE = re.compile(r"(?:joplin-importer|ojr):res:[0-9a-f]{64}")


class ExportExecutionError(RuntimeError):
    pass


@dataclass
class Operation:
    order: int
    action_id: str
    kind: str
    method: str
    endpoint: str
    payload_sha256: str = ""
    mutating: bool = False
    result: str = ""
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
class ExportExecutionResult:
    operations: list[Operation] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    folders_created: int = 0
    notes_created: int = 0
    resources_created: int = 0
    old_roots_trashed: int = 0
    completed_already: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems

    def precondition_fingerprint(self) -> str:
        return sha256_canonical_json(sorted(self.observations))


class _Runner:
    def __init__(
        self,
        client: JoplinClient,
        source: SnapshotReader,
        plan: ExportPlan,
        bodies: dict[str, str],
        *,
        execute: bool,
    ) -> None:
        self.client = client
        self.source = source
        self.plan = plan
        self.bodies = bodies
        self.execute = execute
        self.result = ExportExecutionResult()
        self._order = 0
        self._resource_paths = self._index_resources(source)
        self._resource_ids: dict[str, str] = {}

    def run(self) -> ExportExecutionResult:
        folders = [f for f in self.client.iter_folders() if not f.get("deleted_time")]
        notes = [n for n in self.client.iter_notes() if not n.get("deleted_time")]
        state = self._inspect(folders, notes)
        if self.result.problems:
            return self.result
        if state.completed:
            self.result.completed_already = True
            return self.result

        node_ids = dict(state.node_ids)
        for folder in self.plan.folders:
            if folder.node_id in node_ids:
                continue
            parent_id = node_ids.get(folder.parent_node_id or "")
            if folder.parent_node_id and not parent_id and self.execute:
                raise ExportExecutionError(
                    f"parent folder {folder.parent_node_id} was not created"
                )
            title = self._staging_title(folder) if folder.parent_node_id is None else folder.title
            folder_payload = {
                "title": title,
                "parent_id": parent_id or "",
                "user_data": _folder_user_data(
                    self.plan,
                    folder,
                    state="staging" if folder.parent_node_id is None else "child",
                ),
            }
            op = self._op(
                folder.node_id,
                "create-export-folder",
                "POST",
                "/folders",
                folder_payload,
            )
            if not self.execute:
                op.result = "predicted"
                node_ids[folder.node_id] = f"pending-{folder.node_id}"
                continue
            folder_response = self._request_json("POST", "/folders", folder_payload)
            node_ids[folder.node_id] = str(folder_response["id"])
            op.result = "applied"
            op.joplin_id = str(folder_response["id"])
            self.result.folders_created += 1

        existing_actions = set(state.action_ids)
        for note in self.plan.notes:
            if note.action_id in existing_actions:
                continue
            body = self._checked_body(note)
            resource_ids = {
                digest: self._ensure_resource(note.action_id, digest)
                for digest in note.expected_resource_hashes
            }
            for digest, resource_id in resource_ids.items():
                body = body.replace(
                    RESOURCE_PLACEHOLDER.format(sha256=digest), f":/{resource_id}"
                )
            payload_summary = {
                "title": note.title,
                "parent_node_id": note.parent_node_id,
                "source_page_id": note.source_page_id,
                "body_template_sha256": note.expected_body_sha256,
            }
            op = self._op(note.action_id, "create-export-note", "POST", "/notes", payload_summary)
            if not self.execute:
                op.result = "predicted"
                continue
            parent_id = node_ids.get(note.parent_node_id)
            if not parent_id:
                raise ExportExecutionError(
                    f"destination section {note.parent_node_id} was not created"
                )
            note_payload: dict = {
                "parent_id": parent_id,
                "title": note.title,
                # Joplin supports HTML blocks inside Markdown. Sending this as
                # `body` preserves the audited HTML byte-for-byte; body_html
                # would invoke Joplin's lossy HTML-to-Markdown converter.
                "body": body,
                "source_url": f"onenote://page/{quote(note.source_page_id, safe='')}",
                "source_application": "joplin-importer",
                "order": note.page_order,
            }
            created_ms = _iso_to_ms(note.created_at)
            updated_ms = _iso_to_ms(note.updated_at)
            if created_ms:
                note_payload["user_created_time"] = created_ms
            if updated_ms:
                note_payload["user_updated_time"] = updated_ms
            note_response = self._request_json("POST", "/notes", note_payload)
            op.result = "applied"
            op.joplin_id = str(note_response.get("id", ""))
            self.result.notes_created += 1

        for root in self._root_folders():
            op = self._op(
                root.node_id,
                "verify-staged-root",
                "GET",
                "/folders-and-notes",
                mutating=False,
            )
            op.result = "predicted" if not self.execute else "pending"

        if not self.execute:
            for root in self._root_folders():
                self._op(
                    root.node_id,
                    "promote-export-root",
                    "PUT",
                    f"/folders/<{root.node_id}>",
                    {"title": root.title, "state": "active"},
                ).result = "predicted"
            for old in state.old_roots:
                self._op(
                    str(old["id"]),
                    "trash-old-export-root",
                    "DELETE",
                    f"/folders/{old['id']}",
                    None,
                ).result = "predicted"
            return self.result

        # No old data is touched until the entire staged tree is visible again.
        staged_folders = [f for f in self.client.iter_folders() if not f.get("deleted_time")]
        staged_notes = [n for n in self.client.iter_notes() if not n.get("deleted_time")]
        staged = self._inspect_current_plan(staged_folders, staged_notes)
        expected_nodes = {f.node_id for f in self.plan.folders}
        expected_actions = {n.action_id for n in self.plan.notes}
        if set(staged.node_ids) != expected_nodes or staged.action_ids != expected_actions:
            raise ExportExecutionError(
                "staged export verification failed; the previous export was not touched"
            )
        for op in self.result.operations:
            if op.kind == "verify-staged-root" and op.result == "pending":
                op.result = "ok"

        # Promote first.  A crash can leave two managed versions, but never no
        # usable version; a retry recognises the current plan and finishes cleanup.
        for root in self._root_folders():
            root_id = staged.node_ids[root.node_id]
            payload = {
                "title": root.title,
                "user_data": _folder_user_data(self.plan, root, state="active"),
            }
            op = self._op(
                root.node_id,
                "promote-export-root",
                "PUT",
                f"/folders/{root_id}",
                payload,
            )
            self._request_json("PUT", f"/folders/{root_id}", payload)
            op.result = "applied"
            op.joplin_id = root_id

        promoted_folders = [f for f in self.client.iter_folders() if not f.get("deleted_time")]
        promoted = self._inspect_current_plan(promoted_folders, staged_notes)
        active_root_ids = {
            str(f["id"])
            for f in promoted_folders
            if (_root_marker(f) or {}).get("plan_id") == self.plan.plan_id
            and (_root_marker(f) or {}).get("state") == "active"
        }
        if len(active_root_ids) != len(self._root_folders()):
            raise ExportExecutionError(
                "new export promotion verification failed; the previous export was not touched"
            )
        if set(promoted.node_ids) != expected_nodes:
            raise ExportExecutionError(
                "new export folder verification failed; the previous export was not touched"
            )

        for old in state.old_roots:
            old_id = str(old["id"])
            op = self._op(
                old_id,
                "trash-old-export-root",
                "DELETE",
                f"/folders/{old_id}",
                None,
            )
            delete_response = self.client.transport.request("DELETE", f"/folders/{old_id}")
            if delete_response.status_code >= 300:
                raise ExportExecutionError(
                    f"Joplin rejected trashing old managed notebook {old_id}"
                )
            op.result = "applied"
            op.joplin_id = old_id
            self.result.old_roots_trashed += 1
        return self.result

    def _inspect(self, folders: list[dict], notes: list[dict]):
        external_fingerprint = _external_fingerprint(folders)
        self.result.observations.append(f"external-instance:{external_fingerprint}")
        if self.plan.target_instance_fingerprint not in ("", external_fingerprint):
            self.result.problems.append(
                "target instance fingerprint mismatch: export plan belongs to another profile"
            )

        roots = [f for f in folders if not f.get("parent_id")]
        expected_titles = {f.title for f in self._root_folders()}
        staging_titles = {self._staging_title(f) for f in self._root_folders()}
        staging_titles.update(
            f"_ojr staging {self.plan.plan_id} - {folder.title}"
            for folder in self._root_folders()
        )
        current_roots: list[dict] = []
        old_roots: list[dict] = []
        foreign_staging: list[dict] = []
        unmanaged_collisions: list[str] = []
        old_sets: set[str] = set()

        for root in roots:
            marker = _root_marker(root)
            title = str(root.get("title", ""))
            if marker is None:
                if title in expected_titles or title in staging_titles:
                    unmanaged_collisions.append(title)
                continue
            if marker.get("plan_id") == self.plan.plan_id:
                current_roots.append(root)
            elif marker.get("state") == "staging":
                foreign_staging.append(root)
            elif marker.get("state") == "active":
                old_roots.append(root)
                old_sets.add(str(marker.get("export_set_id", "")))

        if unmanaged_collisions:
            self.result.problems.append(
                "unmanaged top-level notebook conflict(s): "
                + ", ".join(sorted(set(unmanaged_collisions)))
            )
        if foreign_staging:
            self.result.problems.append(
                "a staging export from another plan exists; remove or finish it first"
            )
        if len(old_sets) > 1:
            self.result.problems.append(
                "multiple previous joplin-importer export sets exist; "
                "automatic replacement is ambiguous"
            )
        if old_roots and self.plan.conflict_policy == "fail":
            self.result.problems.append(
                "a previous joplin-importer export exists and conflict policy is 'fail'"
            )
        for old in old_roots:
            marker = _root_marker(old) or {}
            self.result.observations.append(
                f"old-root:{old.get('id')}:{old.get('title')}:{marker.get('export_set_id')}"
            )
        for root in current_roots:
            marker = _root_marker(root) or {}
            self.result.observations.append(
                f"current-root:{root.get('id')}:{root.get('title')}:{marker.get('state')}"
            )

        current = self._inspect_current_plan(folders, notes)
        expected_root_nodes = {f.node_id for f in self._root_folders()}
        active_root_nodes = {
            str((_root_marker(root) or {}).get("node_id"))
            for root in current_roots
            if (_root_marker(root) or {}).get("state") == "active"
        }
        complete = (
            active_root_nodes == expected_root_nodes
            and set(current.node_ids) == {f.node_id for f in self.plan.folders}
            and current.action_ids == {n.action_id for n in self.plan.notes}
            and not old_roots
        )
        if complete:
            self.result.observations.append("export:already-complete")
        return _State(
            node_ids=current.node_ids,
            action_ids=current.action_ids,
            old_roots=old_roots,
            completed=complete,
        )

    def _inspect_current_plan(self, folders: list[dict], notes: list[dict]):
        node_ids: dict[str, str] = {}
        plan_folder_ids: set[str] = set()
        for folder in folders:
            marker = _folder_marker(folder)
            if marker is None or marker.get("plan_id") != self.plan.plan_id:
                continue
            node_id = str(marker.get("node_id", ""))
            if node_id in node_ids:
                raise ExportExecutionError(f"duplicate managed folder marker {node_id}")
            node_ids[node_id] = str(folder["id"])
            plan_folder_ids.add(str(folder["id"]))

        action_ids: set[str] = set()
        action_by_page = {note.source_page_id: note.action_id for note in self.plan.notes}
        for note in notes:
            if str(note.get("parent_id", "")) not in plan_folder_ids:
                continue
            body = str(note.get("body") or note.get("body_html") or "")
            action_match = re.search(
                rf"(?:joplin-importer|ojr):export_plan_id="
                rf"{re.escape(self.plan.plan_id)}\s+"
                rf"(?:joplin-importer|ojr):action_id=([0-9a-f]{{16}})",
                body,
            )
            action_id = action_match.group(1) if action_match else ""
            source_url = str(note.get("source_url", ""))
            if not action_id and source_url.startswith("onenote://page/"):
                source_page_id = unquote(source_url.removeprefix("onenote://page/"))
                action_id = action_by_page.get(source_page_id, "")
            if action_id:
                if action_id in action_ids:
                    raise ExportExecutionError(f"duplicate managed note marker {action_id}")
                action_ids.add(action_id)
        return _CurrentPlan(node_ids=node_ids, action_ids=action_ids)

    def _checked_body(self, note: ExportNote) -> str:
        body = self.bodies.get(note.action_id)
        if body is None:
            raise ExportExecutionError(f"body bundle is missing action {note.action_id}")
        if sha256_text(body) != note.expected_body_sha256:
            raise ExportExecutionError(f"body bundle hash mismatch for action {note.action_id}")
        return body

    def _ensure_resource(self, action_id: str, digest: str) -> str:
        if digest in self._resource_ids:
            return self._resource_ids[digest]
        marker = f"{_RESOURCE_MARKER_PREFIX}{digest}"
        existing: list[dict] = []
        for candidate in (marker, f"{_LEGACY_RESOURCE_MARKER_PREFIX}{digest}"):
            existing = list(
                self.client.search(candidate, item_type="resource", fields=["id", "title"])
            )
            if existing:
                break
        op = self._op(
            action_id,
            "resolve-export-resource",
            "GET",
            "/search",
            mutating=False,
        )
        if existing:
            resource_id = str(existing[0]["id"])
            op.result = "ok"
            op.joplin_id = resource_id
            self._resource_ids[digest] = resource_id
            return resource_id
        op.result = "ok"
        data, filename = self._resource_bytes(digest)
        create = self._op(
            action_id,
            "create-export-resource",
            "POST",
            "/resources",
            {"sha256": digest, "title": marker},
        )
        if not self.execute:
            create.result = "predicted"
            resource_id = f"pending{digest[:25]}"
            self._resource_ids[digest] = resource_id
            return resource_id
        props = {"title": f"{filename or 'resource'} [{marker}]", "filename": filename or digest}
        response = self.client.transport.request(
            "POST",
            "/resources",
            files={"data": (filename or digest, data), "props": (None, json.dumps(props))},
        )
        if response.status_code >= 300:
            raise ExportExecutionError(f"Joplin rejected resource {digest[:12]}")
        resource_id = str(response.json().get("id", ""))
        create.result = "applied"
        create.joplin_id = resource_id
        self._resource_ids[digest] = resource_id
        self.result.resources_created += 1
        return resource_id

    def _resource_bytes(self, digest: str) -> tuple[bytes, str | None]:
        entry = self._resource_paths.get(digest)
        if entry is None:
            raise ExportExecutionError(f"resource {digest[:12]} is missing from source snapshot")
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

    def _root_folders(self) -> list[ExportFolder]:
        return [folder for folder in self.plan.folders if folder.parent_node_id is None]

    def _staging_title(self, folder: ExportFolder) -> str:
        return f"_joplin-importer staging {self.plan.plan_id} - {folder.title}"

    def _request_json(self, method: str, path: str, payload: dict) -> dict:
        response = self.client.transport.request(method, path, json_body=payload)
        if response.status_code >= 300:
            raise ExportExecutionError(f"Joplin rejected {method} {path}")
        return response.json()

    def _op(
        self,
        action_id: str,
        kind: str,
        method: str,
        endpoint: str,
        payload: dict | None = None,
        *,
        mutating: bool = True,
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


@dataclass
class _CurrentPlan:
    node_ids: dict[str, str]
    action_ids: set[str]


@dataclass
class _State(_CurrentPlan):
    old_roots: list[dict]
    completed: bool


def _folder_user_data(plan: ExportPlan, folder: ExportFolder, *, state: str) -> str:
    kind = _ROOT_KIND if folder.parent_node_id is None else _FOLDER_KIND
    return json.dumps(
        {
            _MARKER_NAMESPACE: {
                "kind": kind,
                "schema": 1,
                "state": state,
                "plan_id": plan.plan_id,
                "export_set_id": plan.plan_id,
                "source_snapshot_id": plan.source_snapshot_id,
                "node_id": folder.node_id,
            }
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _folder_marker(folder: dict) -> dict | None:
    value = folder.get("user_data")
    if not value:
        return None
    try:
        payload = value if isinstance(value, dict) else json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    marker = None
    if isinstance(payload, dict):
        marker = payload.get(_MARKER_NAMESPACE)
        if marker is None:
            marker = payload.get(_LEGACY_MARKER_NAMESPACE)
    if not isinstance(marker, dict):
        return None
    if marker.get("kind") not in {_ROOT_KIND, _FOLDER_KIND}:
        return None
    return marker


def _root_marker(folder: dict) -> dict | None:
    marker = _folder_marker(folder)
    if marker is None or marker.get("kind") != _ROOT_KIND:
        return None
    return marker


def _external_fingerprint(folders: list[dict]) -> str:
    managed_roots = {
        str(folder["id"])
        for folder in folders
        if not folder.get("parent_id") and _root_marker(folder) is not None
    }
    excluded = set(managed_roots)
    changed = True
    while changed:
        changed = False
        for folder in folders:
            folder_id = str(folder["id"])
            if folder_id not in excluded and str(folder.get("parent_id", "")) in excluded:
                excluded.add(folder_id)
                changed = True
    return sha256_canonical_json(
        sorted(str(folder["id"]) for folder in folders if str(folder["id"]) not in excluded)
    )[:16]


def export_instance_fingerprint(client: JoplinClient) -> str:
    return _external_fingerprint(
        [folder for folder in client.iter_folders() if not folder.get("deleted_time")]
    )


def profile_object_counts(client: JoplinClient) -> tuple[int, int, int]:
    """Return all folders, notes (including trash/conflicts), and resources."""
    return (
        len(list(client.iter_folders())),
        len(list(client.iter_notes())),
        len(list(client.iter_resources())),
    )


def managed_profile_no_backup_problems(client: JoplinClient) -> list[str]:
    """Prove that every active object is owned by a previous importer export.

    Trashed folders and notes are ignored because replace-managed never edits
    them. Resources are global in Joplin, so every one must retain a recognized
    content-hash title marker, including orphaned resources from an older run.
    """

    problems: list[str] = []
    folders = [folder for folder in client.iter_folders() if not folder.get("deleted_time")]
    notes = [note for note in client.iter_notes() if not note.get("deleted_time")]
    resources = list(client.iter_resources())
    folder_ids = {str(folder.get("id", "")) for folder in folders}
    roots = [folder for folder in folders if not folder.get("parent_id")]

    if not roots:
        problems.append(
            "the active profile has no previous joplin-importer-managed root notebooks"
        )
    unmanaged_folders = [
        str(folder.get("id", "")) for folder in folders if _folder_marker(folder) is None
    ]
    if unmanaged_folders:
        problems.append(
            f"the active profile contains {len(unmanaged_folders)} folder(s) "
            "not owned by joplin-importer"
        )
    unmanaged_roots = [root for root in roots if _root_marker(root) is None]
    if unmanaged_roots:
        problems.append(
            f"the active profile contains {len(unmanaged_roots)} root notebook(s) "
            "not owned by joplin-importer"
        )

    unmanaged_notes = [
        note
        for note in notes
        if note.get("is_conflict")
        or str(note.get("parent_id", "")) not in folder_ids
        or not str(note.get("source_url", "")).startswith("onenote://page/")
    ]
    if unmanaged_notes:
        problems.append(
            f"the active profile contains {len(unmanaged_notes)} note(s) "
            "not owned by joplin-importer"
        )

    unmanaged_resources = [
        resource
        for resource in resources
        if _RESOURCE_MARKER_RE.search(str(resource.get("title", ""))) is None
    ]
    if unmanaged_resources:
        problems.append(
            f"the profile contains {len(unmanaged_resources)} resource(s) "
            "not owned by joplin-importer"
        )
    return problems


def dry_run_export(
    client: JoplinClient,
    source: SnapshotReader,
    plan: ExportPlan,
    bodies: dict[str, str],
    approval: ExportApproval,
    *,
    plan_sha256: str,
    approval_sha256: str,
    output_dir: Path,
) -> tuple[ExportDryRunReceipt, ExportExecutionResult]:
    if client.transport.mode is not TransportMode.READ_ONLY:
        raise ExportExecutionError("export dry-run requires a READ_ONLY transport")
    if approval.export_plan_sha256 != plan_sha256:
        raise ExportExecutionError("approval digest does not match the export plan")
    client.probe_capabilities()
    folder_count, note_count, resource_count = profile_object_counts(client)
    result = _Runner(client, source, plan, bodies, execute=False).run()
    sent = client.transport.mutating_requests_sent()
    if sent:
        result.problems.append(f"{len(sent)} mutating request(s) escaped the read-only guard")
    fingerprint = export_instance_fingerprint(client)
    receipt = ExportDryRunReceipt(
        created_at_utc=now_utc_iso(),
        plan_id=plan.plan_id,
        export_plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        target_instance_fingerprint=fingerprint,
        live_folder_count=folder_count,
        live_note_count=note_count,
        live_resource_count=resource_count,
        live_precondition_fingerprint=result.precondition_fingerprint(),
        result="ok" if result.ok else "failed",
        problems=list(result.problems),
        mutating_requests_sent=len(sent),
    )
    _write_dry_outputs(receipt, result, client.transport.ledger, output_dir)
    return receipt, result


def apply_export(
    client: JoplinClient,
    source: SnapshotReader,
    plan: ExportPlan,
    bodies: dict[str, str],
    approval: ExportApproval,
    receipt: ExportDryRunReceipt,
    *,
    plan_sha256: str,
    approval_sha256: str,
    receipt_sha256: str,
    output_dir: Path,
    jex_backup_path: Path | None,
    confirm_sync_complete: bool,
    confirm_full_replace: bool,
    confirm_empty_profile_no_backup: bool = False,
    confirm_managed_profile_no_backup: bool = False,
) -> tuple[ExportApplyReceipt, ExportExecutionResult]:
    problems = _apply_preflight(
        plan,
        approval,
        receipt,
        plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        jex_backup_path=jex_backup_path,
        confirm_sync_complete=confirm_sync_complete,
        confirm_full_replace=confirm_full_replace,
        confirm_empty_profile_no_backup=confirm_empty_profile_no_backup,
        confirm_managed_profile_no_backup=confirm_managed_profile_no_backup,
    )
    if problems:
        raise ExportExecutionError("; ".join(problems))
    if client.transport.mode is not TransportMode.WRITE_ENABLED:
        raise ExportExecutionError("export apply requires a WRITE_ENABLED transport")
    client.probe_capabilities()
    if confirm_empty_profile_no_backup:
        live_counts = profile_object_counts(client)
        if live_counts != (0, 0, 0):
            raise ExportExecutionError(
                "empty-profile no-backup confirmation is invalid: live Joplin has "
                f"{live_counts[0]} folder(s), {live_counts[1]} note(s), and "
                f"{live_counts[2]} resource(s)"
            )
    if confirm_managed_profile_no_backup:
        ownership_problems = managed_profile_no_backup_problems(client)
        if ownership_problems:
            raise ExportExecutionError("; ".join(ownership_problems))

    # Re-run the exact read-only compiler immediately before mutation.
    preflight_result = _Runner(client, source, plan, bodies, execute=False).run()
    if not preflight_result.ok:
        raise ExportExecutionError("; ".join(preflight_result.problems))
    if preflight_result.precondition_fingerprint() != receipt.live_precondition_fingerprint:
        raise ExportExecutionError(
            "Joplin export preconditions changed after dry-run; generate a new receipt"
        )

    result = _Runner(client, source, plan, bodies, execute=True).run()
    if not result.ok:
        raise ExportExecutionError("; ".join(result.problems))
    output_dir.mkdir(parents=True, exist_ok=True)
    apply_receipt = ExportApplyReceipt(
        created_at_utc=now_utc_iso(),
        plan_id=plan.plan_id,
        export_plan_sha256=plan_sha256,
        approval_sha256=approval_sha256,
        dry_run_receipt_sha256=receipt_sha256,
        jex_backup_path=str(jex_backup_path) if jex_backup_path else "",
        jex_backup_sha256=sha256_file(jex_backup_path) if jex_backup_path else "",
        operator_confirmed_sync_complete=confirm_sync_complete,
        operator_confirmed_full_replace=confirm_full_replace,
        operator_confirmed_empty_profile_no_backup=confirm_empty_profile_no_backup,
        operator_confirmed_managed_profile_no_backup=confirm_managed_profile_no_backup,
        folders_created=result.folders_created,
        notes_created=result.notes_created,
        resources_created=result.resources_created,
        old_roots_trashed=result.old_roots_trashed,
    )
    (output_dir / "export-apply-receipt.json").write_text(
        apply_receipt.model_dump_json(indent=2), encoding="utf-8"
    )
    _write_operations(result, output_dir / "export-apply-operations.jsonl")
    return apply_receipt, result


def _apply_preflight(
    plan: ExportPlan,
    approval: ExportApproval,
    receipt: ExportDryRunReceipt,
    *,
    plan_sha256: str,
    approval_sha256: str,
    jex_backup_path: Path | None,
    confirm_sync_complete: bool,
    confirm_full_replace: bool,
    confirm_empty_profile_no_backup: bool,
    confirm_managed_profile_no_backup: bool,
) -> list[str]:
    problems: list[str] = []
    if approval.export_plan_sha256 != plan_sha256:
        problems.append("approval digest does not match the export plan")
    if receipt.export_plan_sha256 != plan_sha256:
        problems.append("dry-run receipt refers to a different export plan")
    if receipt.approval_sha256 != approval_sha256:
        problems.append("dry-run receipt refers to a different approval")
    if receipt.plan_id != plan.plan_id or receipt.result != "ok":
        problems.append("dry-run receipt is not a successful receipt for this plan")
    has_jex = jex_backup_path is not None and jex_backup_path.exists()
    if (
        not has_jex
        and not confirm_empty_profile_no_backup
        and not confirm_managed_profile_no_backup
    ):
        problems.append(
            "a current JEX backup is required, or explicitly confirm a proven empty "
            "or fully joplin-importer-managed profile"
        )
    if confirm_empty_profile_no_backup and confirm_managed_profile_no_backup:
        problems.append("choose only one no-backup confirmation")
    if confirm_empty_profile_no_backup:
        if plan.conflict_policy != "fail":
            problems.append("empty-profile no-backup is allowed only with conflict policy 'fail'")
        receipt_counts = (
            receipt.live_folder_count,
            receipt.live_note_count,
            receipt.live_resource_count,
        )
        if receipt_counts != (0, 0, 0):
            problems.append(
                "dry-run receipt does not prove an empty Joplin profile (0 folders/notes/resources)"
            )
    if confirm_managed_profile_no_backup:
        if plan.conflict_policy != "replace-managed":
            problems.append(
                "managed-profile no-backup is allowed only with conflict policy 'replace-managed'"
            )
        if receipt.live_folder_count <= 0 or receipt.live_note_count <= 0:
            problems.append(
                "dry-run receipt does not prove a non-empty profile eligible "
                "for managed replacement"
            )
    if not confirm_sync_complete:
        problems.append("operator must confirm Joplin synchronization has completed")
    if plan.conflict_policy == "replace-managed" and not confirm_full_replace:
        problems.append("operator must confirm replacement of the entire old managed export")
    return problems


def _write_dry_outputs(
    receipt: ExportDryRunReceipt,
    result: ExportExecutionResult,
    ledger: list[LedgerEntry],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "receipt": receipt.model_dump(mode="json"),
        "operations": [op.to_dict() for op in result.operations],
        "problems": result.problems,
    }
    (output_dir / "export-dry-run-report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "receipt.json").write_text(
        receipt.model_dump_json(indent=2), encoding="utf-8"
    )
    _write_operations(result, output_dir / "operations.jsonl")
    with (output_dir / "transport-ledger.jsonl").open("w", encoding="utf-8") as fh:
        for entry in ledger:
            fh.write(
                json.dumps(
                    {
                        "method": entry.method,
                        "url": entry.url,
                        "status_code": entry.status_code,
                        "error": entry.error,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def _write_operations(result: ExportExecutionResult, path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for operation in result.operations:
            fh.write(json.dumps(operation.to_dict(), ensure_ascii=False) + "\n")


def _iso_to_ms(value: str | None) -> int | None:
    seconds = epoch_seconds(value)
    return int(seconds * 1000) if seconds is not None else None
