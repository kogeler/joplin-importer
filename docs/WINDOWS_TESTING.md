# Windows Live-Validation Runbook

Run every live OneNote/Joplin operation on the Windows machine. Keep tokens,
snapshots, plans, receipts, reports, ledgers, JEX files, and quarantine files in
Git-ignored locations.

The Graph backend is excluded from this accepted workflow: it has not been
finished or tested against a real Microsoft account. See [TODO.md](../TODO.md).

## 1. Bootstrap and automated checks

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.venv\Scripts\python.exe -m pip install -r requirements-windows-lock.txt
.venv\Scripts\python.exe -m pip install --no-deps -e .
.venv\Scripts\joplin-importer.exe --version
.venv\Scripts\python.exe -m pytest
.venv\Scripts\ruff.exe check src tests
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe -m joplin_importer.schemas schemas
```

## 2. Prepare Joplin

1. Open Joplin Desktop and select the intended profile.
2. Enable Options → Web Clipper (local Data API).
3. Save the API token in the ignored local `token` file; never echo it.
4. Wait until synchronization has completed.

```powershell
.venv\Scripts\joplin-importer.exe doctor --token-file .\token --json
```

Save `export_instance_fingerprint` for the plan. Doctor is read-only.

The opt-in live smoke test must also pass:

```powershell
.venv\Scripts\python.exe -m pytest -m live_joplin `
    tests\integration\test_live_smoke.py
```

If another client or sync changes Joplin during a proof run, let it settle and
start the proof again.

## 3. Build the source snapshot

The supported restore source is the newest local OneNote backup:

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend backup `
    --output artifacts\snapshots\source-backup
```

The adapter dynamically searches below the local OneNote application-data
tree, without hard-coding a localized backup folder name. To override it:

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend backup `
    --backup-root <backup-directory> `
    --output artifacts\snapshots\source-backup
```

It selects only the newest file for each logical section. Older dated versions
are counted and skipped, never merged or used as fallback. Confirm
`coverage_status` is `complete`, checksums validate, and `errors.jsonl` has no
unexplained extraction failure. Absolute source paths must not appear in the
snapshot.

An optional COM scan can be used to compare current live OneNote state:

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend com `
    --output artifacts\snapshots\source-com
```

If a small known set of page IDs reliably crashes `GetPageContent`, pass an
explicit quarantine file as documented in
[ONENOTE_QUARANTINE.md](ONENOTE_QUARANTINE.md). Every
matched/stale entry makes the snapshot partial. Do not grow quarantine merely
to make a scan green, and never use a quarantined COM snapshot for export.

On the affected real notebook this condition was not small or stable. More
crashing pages appeared after known IDs were skipped, and Office Quick/Online
Repair plus reopen/re-download attempts did not restore reliable COM reads.
Quarantine therefore did not bypass the defect. Stop if the list grows; do not
repeat the historical skip-and-restart experiment beyond its 20-entry operator
cap. See
[ONENOTE_COM_KNOWN_ISSUE.md](ONENOTE_COM_KNOWN_ISSUE.md) and switch to the
complete newest backup.

## 4. Optional state analysis

```powershell
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin-analysis
.venv\Scripts\joplin-importer.exe compare artifacts\snapshots\source-backup `
    artifacts\snapshots\joplin-analysis --output artifacts\reports\audit
```

Review `summary.html`, JSON/CSV reports, and `extractor-errors.csv`. Missing,
incomplete, ambiguous, and target-extra findings explain the old state; they
do not drive a merge or deletion. The former partial repair commands are not
supported.

## 5. Plan the complete export

For an empty/new profile:

```powershell
.venv\Scripts\joplin-importer.exe export-plan `
    --source-snapshot artifacts\snapshots\source-backup `
    --on-conflict fail `
    --target-fingerprint <export-instance-fingerprint> `
    --output artifacts\export-plan.json
.venv\Scripts\joplin-importer.exe export-approve artifacts\export-plan.json `
    --operator "<operator>" --output artifacts\export-approval.json
```

For a later refresh, use `--on-conflict replace-managed`. It may replace only
the complete previous `joplin-importer` export set; any unmanaged title collision stops the
operation.

## 6. Mandatory no-mutation dry-run

```powershell
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin-before-export-dry-run
.venv\Scripts\joplin-importer.exe export-dry-run artifacts\export-plan.json `
    --approval-file artifacts\export-approval.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --token-file .\token --output artifacts\reports\export-dry-run
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin-after-export-dry-run
```

All of the following must hold:

* `receipt.json` has `result: "ok"` and `mutating_requests_sent: 0`;
* `transport-ledger.jsonl` contains only requests actually sent, all GET;
* predicted operations contain no applied operation;
* before/after manifest `inventory_hash` values are identical;
* all conflicts, ownership checks, and planned object counts are understood.

Never use real `export-apply` to debug a failed dry-run.

## 7. Apply

Normally, first export all Joplin data to a current JEX and confirm sync again:

```powershell
.venv\Scripts\joplin-importer.exe export-apply artifacts\export-plan.json `
    --approval-file artifacts\export-approval.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --dry-run-receipt artifacts\reports\export-dry-run\receipt.json `
    --jex-backup artifacts\backup.jex --confirm-sync-complete `
    --token-file .\token --output artifacts\reports\export-apply
```

If Joplin refuses to export a completely empty profile, use
`--confirm-empty-profile-no-backup`; both dry-run and immediate preflight must
prove zero folders, notes (including trash/conflicts), and resources. For a
profile containing only an older managed export, the distinct
`--confirm-managed-profile-no-backup` waiver is available and requires an
immediate proof that every active object/resource is owned by `joplin-importer`.

`replace-managed` additionally requires `--confirm-full-replace`. The exporter
creates and verifies the whole staged new tree before promoting it, then moves
the whole old managed set to Joplin trash. It never permanently deletes or
touches unmanaged objects.

## 8. Validate the result

After apply and sync:

```powershell
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin-after-export
.venv\Scripts\joplin-importer.exe export-validate artifacts\export-plan.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --target-snapshot artifacts\snapshots\joplin-after-export `
    --strict-profile --output artifacts\reports\export-validation.json
```

Acceptance requires zero validation issues: all planned roots/folders exist,
exactly one managed note represents every source page ID, semantic content and
resources match, and no unexpected note exists inside managed roots. Keep the
full evidence set locally and ignored.
