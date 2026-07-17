# Workspace and Artifact Format

The repository is not a note workspace. Live data and generated evidence are
kept in ignored operator-chosen paths, conventionally arranged as follows:

```text
joplin-importer/
├── token                              # Joplin token; never commit
└── artifacts/                         # all generated private data
    ├── snapshots/
    │   ├── source-backup/
    │   ├── joplin-before-export-dry-run/
    │   ├── joplin-after-export-dry-run/
    │   └── joplin-after-export/
    ├── reports/
    │   ├── audit/
    │   ├── export-dry-run/
    │   └── export-apply/
    ├── export-plan.json
    ├── export-plan.bodies/
    ├── export-approval.json
    ├── onenote-quarantine.json
    ├── backup.jex
    └── export-validation.json
```

`token` and the entire root `artifacts/` directory are ignored. CLI output
paths remain configurable, but using another location makes the operator
responsible for adding an equally narrow ignore rule before generating data.

## Snapshot directory

Finalized snapshots contain `manifest.json`, `scan-metadata.json`, a derived
`inventory.sqlite`, optional `errors.jsonl`, record/raw/semantic files under
`pages/`, and content-addressed resources under `resources/`. Interrupted
scans use a sibling `<name>.staging` directory.

The complete layout and hashing contract are defined in
[SNAPSHOT_FORMAT.md](SNAPSHOT_FORMAT.md). Never edit a finalized snapshot.

## Audit report directory

`compare --output PATH` writes:

- `summary.html` and `summary.json`;
- `matches.csv` and finding-specific CSV files;
- `extractor-errors.csv` copied from partial snapshot evidence.

Reports may contain titles, content excerpts, identifiers, and local recovery
context. Treat them as private even though the HTML report does not execute
source scripts.

## Export plan bundle

For `export-plan.json`, the sibling `export-plan.bodies/` directory contains
one `<action-id>.html` body for every planned note. The plan records the
expected SHA-256 of each body. Move or archive the JSON and body directory
together; missing or changed bodies make apply fail.

`export-approval.json` contains the exact plan digest. It is not reusable for
a modified or regenerated plan.

## Dry-run evidence

The dry-run output directory contains:

| File | Purpose |
| --- | --- |
| `receipt.json` | versioned digest/profile/precondition proof |
| `export-dry-run-report.json` | receipt, predicted operations, and problems |
| `operations.jsonl` | ordered predicted operation records |
| `transport-ledger.jsonl` | redacted requests actually sent; must be GET-only |

The receipt does not replace the independent before/after Joplin snapshots.

## Apply and validation evidence

The apply output directory contains `export-apply-receipt.json` and
`export-apply-operations.jsonl`. The receipt binds the plan, approval, dry-run
receipt, backup hash/waiver confirmations, and result counts.

`export-validate --output FILE` writes one versioned JSON report. Keep it with
the post-export target snapshot and apply evidence.

## Ignore boundary

The repository `.gitignore` has two private-data boundaries: `/token` and
`/artifacts/`. It deliberately does not ignore plan, approval, receipt, JEX,
or snapshot names globally because broad patterns can hide fixtures or other
intentional repository content. Verify `git status` before every commit.
