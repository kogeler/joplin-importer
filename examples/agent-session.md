# Example Agent Session

This is the shape of the canonical workflow. Values are synthetic and JSON is
abbreviated to the relevant fields. Live commands run only on the Windows test
machine; development and CI use fake servers.

## Read-only analysis

```console
> .venv\Scripts\joplin-importer.exe doctor --token-file .\token --json
{
  "ping": "ok",
  "export_instance_fingerprint": "8a4d...",
  "mutating_requests_sent": 0
}

> .venv\Scripts\joplin-importer.exe scan-onenote --backend backup --output artifacts\snapshots\source
snapshot written to artifacts\snapshots\source (complete)

> .venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token --output artifacts\snapshots\target
snapshot written to artifacts\snapshots\target (complete)

> .venv\Scripts\joplin-importer.exe compare artifacts\snapshots\source artifacts\snapshots\target --output artifacts\reports\audit --json
{
  "reports": "artifacts\\reports\\audit",
  "source_pages": 42,
  "target_notes": 39,
  "findings_by_kind": {"missing": 3}
}
```

Exit `1` from `compare` means the audit found differences; it does not mean
that a write was attempted. Review `artifacts\reports\audit\summary.html`.

## Plan and no-mutation proof

```console
> .venv\Scripts\joplin-importer.exe export-plan --source-snapshot artifacts\snapshots\source --on-conflict fail --target-fingerprint 8a4d... --output artifacts\export-plan.json --json
{"plan_id": "9d20...", "folders": 8, "notes": 42, "conflict_policy": "fail", ...}

> .venv\Scripts\joplin-importer.exe export-approve artifacts\export-plan.json --operator "operator" --output artifacts\export-approval.json --json
{"approval": "artifacts\\export-approval.json", "plan_sha256": "731c..."}

> .venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token --output artifacts\snapshots\before-dry-run
snapshot written to artifacts\snapshots\before-dry-run (complete)

> .venv\Scripts\joplin-importer.exe export-dry-run artifacts\export-plan.json --approval-file artifacts\export-approval.json --source-snapshot artifacts\snapshots\source --token-file .\token --output artifacts\reports\export-dry-run --json
{
  "result": "ok",
  "operations": 58,
  "mutating_requests_sent": 0,
  "problems": []
}

> .venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token --output artifacts\snapshots\after-dry-run
snapshot written to artifacts\snapshots\after-dry-run (complete)
```

Before apply, verify that the two Joplin snapshot inventory hashes match and
that `artifacts\reports\export-dry-run\transport-ledger.jsonl` contains only
successful GET requests. A failed check invalidates the evidence chain; do not
patch the receipt.

## Apply and strict validation

After creating a current JEX backup and confirming Joplin sync:

```console
> .venv\Scripts\joplin-importer.exe export-apply artifacts\export-plan.json --approval-file artifacts\export-approval.json --source-snapshot artifacts\snapshots\source --dry-run-receipt artifacts\reports\export-dry-run\receipt.json --jex-backup artifacts\backup.jex --confirm-sync-complete --token-file .\token --output artifacts\reports\export-apply --json
{"folders_created": 8, "notes_created": 42, "resources_created": 6, ...}

> .venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token --output artifacts\snapshots\after-export
snapshot written to artifacts\snapshots\after-export (complete)

> .venv\Scripts\joplin-importer.exe export-validate artifacts\export-plan.json --source-snapshot artifacts\snapshots\source --target-snapshot artifacts\snapshots\after-export --strict-profile --output artifacts\reports\export-validation.json --json
{"result": "ok", "validated_notes": 42, "planned_notes": 42, "issues": {}}
```

For refresh of a previous managed export, generate a new plan with
`--on-conflict replace-managed` and pass `--confirm-full-replace` to apply.
Never use that policy to resolve a collision with an unmanaged notebook.
