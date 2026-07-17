# Deterministic Full Export

This is the only supported Joplin mutation workflow. It recreates a complete
OneNote snapshot in Joplin, is separate from diagnostic audit/matching, and
performs no fuzzy matching or page-level merge. Every
source notebook is created as a top-level Joplin notebook, with section groups,
sections, pages, and recovered resources underneath it.

Use a complete local-backup snapshot. COM is intended for current-state
analysis and may be incomplete when quarantined. Microsoft Graph is not an
accepted export source because real cloud coverage and end-to-end recovery
have not been completed ([TODO.md](../TODO.md)).

## Conflict policies

`fail` is the default. Before the first write, `joplin-importer` inventories the live
profile. Any unmanaged top-level notebook whose title equals a source notebook
title stops the complete operation. A previous managed export also stops it.

`replace-managed` is intended for later refreshes. All top-level notebooks in
the previous export set must carry valid `joplin-importer` ownership metadata and share one
export-set ID. The complete new tree is created under temporary root names and
re-read from Joplin. The new roots are promoted first; only after that succeeds
are *all* roots of the previous managed set moved to Joplin's trash. This also
removes managed notebooks that disappeared from the new source snapshot.
Unmanaged notebooks are never updated or trashed under either policy.
The complete ownership and blocking-condition matrix is in
[CONFLICTS.md](CONFLICTS.md).

## First export

The source snapshot must have `coverage_status: complete` and valid checksums.

```powershell
.venv\Scripts\joplin-importer.exe export-plan `
    --source-snapshot artifacts\snapshots\source-backup `
    --on-conflict fail --target-fingerprint <export-instance-fingerprint> `
    --output artifacts\export-plan.json

.venv\Scripts\joplin-importer.exe export-approve artifacts\export-plan.json `
    --operator "<operator>" --output artifacts\export-approval.json

.venv\Scripts\joplin-importer.exe export-dry-run artifacts\export-plan.json `
    --approval-file artifacts\export-approval.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --token-file .\token --output artifacts\reports\export-dry-run
```

The dry-run receipt must be `ok`, its mutation count must be zero, its transport
ledger must contain only `GET`, and independent before/after Joplin snapshots
must have the same inventory hash.

After creating a current JEX backup and waiting for synchronization:

```powershell
.venv\Scripts\joplin-importer.exe export-apply artifacts\export-plan.json `
    --approval-file artifacts\export-approval.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --dry-run-receipt artifacts\reports\export-dry-run\receipt.json `
    --jex-backup artifacts\before-export.jex --confirm-sync-complete `
    --token-file .\token --output artifacts\reports\export-apply
```

`--confirm-full-replace` is additionally required when the plan uses
`replace-managed`. Permanent deletion is never requested; old roots go to the
Joplin trash.

Joplin may refuse to create a JEX when a new profile contains no data. In that
single case, `--confirm-empty-profile-no-backup` may be used instead of
`--jex-backup`. The dry-run receipt and a new live preflight must both prove
exactly zero folders, notes (including trash/conflicts), and resources. The
plan must use `--on-conflict fail`; this exception is forbidden for
`replace-managed` and stops before writing if any object appears.

For a `replace-managed` refresh where the profile contains only an earlier
`joplin-importer` export, `--confirm-managed-profile-no-backup` is a separate narrow
exception. Immediately before the first write it re-reads the profile and
requires every active folder and note, plus every global resource, to carry
valid `joplin-importer` ownership or content-hash metadata. Any unmanaged object or Joplin
conflict aborts before mutation. The previous managed roots are moved only to
Joplin's trash and only after the complete staged replacement is verified.

## Validation

After apply and synchronization, take a new read-only Joplin snapshot. Validate
at minimum:

* the number and titles of top-level notebooks;
* every planned folder ownership marker;
* exactly one exported note for every source page ID;
* normalized semantic content for every note;
* every planned resource reference and resource content hash;
* absence of unexpected notes inside the managed export roots.

For a profile that was empty before export, run the strict validator:

```powershell
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin-after-full-export
.venv\Scripts\joplin-importer.exe export-validate artifacts\export-plan.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --target-snapshot artifacts\snapshots\joplin-after-full-export `
    --strict-profile --output artifacts\reports\export-validation.json
```

Plans, approvals, bodies, dry-run output, apply output, snapshots, tokens, and
JEX files are private runtime artifacts and are ignored by Git.

The former selective `_OneNote Recovery` workflow is intentionally not a
fallback: it is an unsupported prototype retained internally only for analysis
and debugging.
