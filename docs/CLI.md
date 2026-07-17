# CLI Reference

The public command is `joplin-importer`. It supports read-only analysis and
one deterministic complete-export workflow; the legacy partial-repair command
group is intentionally not public.

## Common output options

Every command accepts:

| Option | Behavior |
| --- | --- |
| `--json` | write the command-specific JSON object to stdout |
| `--verbose` | write progress details to stderr where supported |
| `--quiet` | suppress ordinary human-readable stdout |

JSON fields are command-specific; there is no shared envelope. Diagnostics
and errors go to stderr. JSON is UTF-8 and emitted with non-ASCII characters
preserved.

Commands that access Joplin also accept:

| Option | Behavior |
| --- | --- |
| `--token-file PATH` | read the Joplin token from an ignored local file |
| `--token-env NAME` | read the token from the named environment variable |
| `--base-url URL` | Joplin Data API URL; defaults to `http://127.0.0.1:41184` |

Exactly one secret source should be used. A raw token is never accepted as a
command-line value.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success; no reportable findings where relevant |
| `1` | partial scan, audit findings, unsafe dry-run, or validation issues |
| `2` | invalid syntax, option combination, or input artifact |
| `3` | connectivity, extraction, execution, or other operational failure |

## Analysis commands

### `doctor`

```text
joplin-importer doctor [JOPLIN OPTIONS] [--json]
```

Uses read-only transport to probe the local Data API, its capabilities, and
the profile's `export_instance_fingerprint`. The fingerprint binds a plan to
the intended Joplin profile.

### `scan-onenote`

```text
joplin-importer scan-onenote --backend backup|com|graph --output PATH
    [--backup-root PATH] [--notebook TITLE] [--include-recycle-bin]
    [--quarantine-file PATH] [--resume] [--client-id ID]
```

- `backup` is the supported complete-export source. Without `--backup-root`,
  it discovers the localized OneNote backup directory and selects the newest
  file for each logical section.
- `com` is Windows-only and useful for current-state analysis. A quarantine
  skips exact crashing page IDs and always yields partial coverage when used.
- `graph` is experimental, requires `--client-id`, and cannot be used for
  complete export.

Exit `1` means the snapshot was finalized with partial coverage. Resume an
interrupted staging directory with `--resume`.

### `scan-joplin`

```text
joplin-importer scan-joplin [JOPLIN OPTIONS] --output PATH
    [--no-resources] [--resume]
```

Inventories the active Joplin profile through read-only Data API requests.
Resources are downloaded into the snapshot unless `--no-resources` is used.

### `compare`

```text
joplin-importer compare SOURCE_SNAPSHOT TARGET_SNAPSHOT --output PATH
    [--additional-source PATH ...]
```

Writes `summary.html`, `summary.json`, matches/findings CSV files, and
`extractor-errors.csv`. Exit `1` means findings other than
`representation-only` exist. Reports are diagnostic and never feed the
exporter.

## Complete export commands

### `export-plan`

```text
joplin-importer export-plan --source-snapshot PATH --output PLAN.json
    [--on-conflict fail|replace-managed]
    [--target-fingerprint FINGERPRINT]
```

Requires a non-empty, complete, checksum-valid non-Graph source snapshot. It
writes the versioned plan plus a sibling `PLAN.bodies/` directory containing
hash-bound note bodies. `fail` is the default conflict policy.

### `export-approve`

```text
joplin-importer export-approve PLAN.json --output APPROVAL.json
    [--operator NAME]
```

Validates the plan and writes an approval containing the exact plan SHA-256.
Any plan edit invalidates the approval.

### `export-dry-run`

```text
joplin-importer export-dry-run PLAN.json --approval-file APPROVAL.json
    --source-snapshot PATH [JOPLIN OPTIONS] --output DIRECTORY
```

Revalidates the artifact chain and compiles predicted operations against live
Joplin using `READ_ONLY` transport. The output includes `receipt.json`,
`export-dry-run-report.json`, `operations.jsonl`, and
`transport-ledger.jsonl`. Exit `1` means preconditions are unsafe.

The receipt is usable only when `result` is `ok` and
`mutating_requests_sent` is zero. The independent before/after snapshot proof
from [WINDOWS_TESTING.md](WINDOWS_TESTING.md) is still required.

### `export-apply`

```text
joplin-importer export-apply PLAN.json --approval-file APPROVAL.json
    --source-snapshot PATH --dry-run-receipt RECEIPT.json
    --jex-backup BACKUP.jex --confirm-sync-complete
    [--confirm-full-replace] [JOPLIN OPTIONS] --output DIRECTORY
```

This is the only public command that enables Joplin writes. It reruns the
read-only preflight immediately before mutation and refuses changed
preconditions. `replace-managed` also requires `--confirm-full-replace`.

The mutually exclusive `--confirm-empty-profile-no-backup` and
`--confirm-managed-profile-no-backup` options are narrow substitutes for
`--jex-backup`; their live proof requirements are defined in
[FULL_EXPORT.md](FULL_EXPORT.md). `--dry-run` is a compatibility route through
the same no-mutation implementation as `export-dry-run`.

### `export-validate`

```text
joplin-importer export-validate PLAN.json --source-snapshot PATH
    --target-snapshot PATH --output REPORT.json [--strict-profile]
```

Validates folder structure, source-page identity, semantic content, and
resources using a post-export Joplin snapshot. `--strict-profile` also
requires total active folder/note counts to match the plan. Exit `1` means at
least one issue exists.

## Unsupported command names

`plan`, `approve`, `dry-run`, and `apply` were the selective repair prototype.
They must not be used as aliases for the `export-*` workflow because their
artifact and safety contracts are different.
