# joplin-importer

Read-only OneNote/Joplin snapshot analysis and deterministic complete
backup-to-Joplin export. `joplin-importer` is designed for operators and
automation that need explicit provenance, machine-readable output, and a
mutation boundary that can be verified before any live write.

> **Safety first.** `doctor`, scans, comparison, and `export-dry-run` cannot
> send mutating Joplin requests. Full export accepts only a complete,
> checksum-valid source snapshot and never performs a fuzzy or partial merge.

Coding agents should start with [AGENTS.md](AGENTS.md).

## How it works

There are two supported workflows:

1. **Analysis (read-only):** inventory OneNote and Joplin as immutable
   snapshots, normalize their content, match likely equivalents, and produce
   HTML/JSON/CSV reports.
2. **Complete export (no merge):** compile every notebook, section group,
   section, page, and resource from one complete source snapshot; bind an
   approval to the plan; prove the plan against live Joplin in a no-mutation
   dry-run; then stage, verify, and promote the whole managed tree.

The unsupported partial-repair prototype is not exposed by the public CLI.
Its `repair/` package and schemas remain only for historical artifact analysis,
debugging, and regression coverage.

Microsoft Graph is an experimental, fake-server-tested read-only scanner. It
is not a complete cloud restore source; remaining work is tracked in
[TODO.md](TODO.md).

## Installation

Requires CPython **>= 3.14** on Windows or Linux. Live OneNote COM and Joplin
validation are performed only on the Windows test machine.

Install a tagged release:

```sh
python -m pip install \
  "git+https://github.com/kogeler/joplin-importer.git@v0.2.0"
joplin-importer --version
```

From a checkout, the Makefile owns the project virtual environment and uses
the committed dependency locks:

```sh
make venv
make check
make help
```

`make container-check` runs the same offline checks in Podman with the
official Python 3.14 image. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
development and dependency workflow.

## Five-minute quick start

On the Windows test machine, enable Joplin's Web Clipper service and save its
token in the ignored local file `token`. The supported source is the newest
complete local OneNote backup.

```powershell
.venv\Scripts\joplin-importer.exe doctor --token-file .\token --json
.venv\Scripts\joplin-importer.exe scan-onenote --backend backup `
    --output artifacts\snapshots\source-backup
.venv\Scripts\joplin-importer.exe scan-joplin --token-file .\token `
    --output artifacts\snapshots\joplin
.venv\Scripts\joplin-importer.exe compare artifacts\snapshots\source-backup artifacts\snapshots\joplin `
    --output artifacts\reports\audit
```

Open `artifacts\reports\audit\summary.html` to review the read-only comparison. To
restore the whole backup, use the export fingerprint reported by `doctor`:

```powershell
.venv\Scripts\joplin-importer.exe export-plan `
    --source-snapshot artifacts\snapshots\source-backup `
    --on-conflict fail --target-fingerprint <fingerprint> `
    --output artifacts\export-plan.json
.venv\Scripts\joplin-importer.exe export-approve artifacts\export-plan.json `
    --operator "<operator>" --output artifacts\export-approval.json
.venv\Scripts\joplin-importer.exe export-dry-run artifacts\export-plan.json `
    --approval-file artifacts\export-approval.json `
    --source-snapshot artifacts\snapshots\source-backup `
    --token-file .\token --output artifacts\reports\export-dry-run
```

Do not proceed unless the receipt is `ok`, its mutation count is zero, its
transport ledger is GET-only, and independent before/after Joplin snapshots
have identical inventory hashes. The JEX backup gate, apply command, and
strict validation sequence are in [docs/FULL_EXPORT.md](docs/FULL_EXPORT.md).

## Architecture overview

```text
OneNote backup / COM / experimental Graph       Joplin Data API
                    |                                  |
                    +----------- snapshots -----------+
                                    |
                 +------------------+------------------+
                 |                                     |
          diagnostic analysis                  complete export
      normalize -> match -> detect       plan -> dry-run -> apply
                 |                                     |
       HTML / JSON / CSV reports          validate managed tree
             (read-only)                    (no page merge)
```

All Joplin traffic passes through one guarded transport. Snapshot records and
export artifacts use versioned models and deterministic hashing. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Supported / not supported

| Supported | Out of scope |
| --- | --- |
| complete local OneNote backup as export source | partial page merge or repair |
| read-only backup, COM, and Joplin snapshots | modifying OneNote or its live cache |
| semantic comparison and diagnostic reports | using fuzzy matches to drive writes |
| complete managed export and full-set refresh | taking over unmanaged Joplin notebooks |
| resources, ownership checks, strict validation | permanent deletion from Joplin |
| Python 3.14 on Windows/Linux | Graph as a production restore source |

## Versioning

The root `.version` file is the single version source. The project follows
Semantic Versioning; every pull request raises the version and updates
[CHANGELOG.md](CHANGELOG.md). Merges to `main` are validated, tagged as
`vX.Y.Z`, and published by GitHub Actions. See
[docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md).

## Documentation

| Document | Purpose |
| --- | --- |
| [AGENTS.md](AGENTS.md) | canonical agent runbook and safety prohibitions |
| [docs/CLI.md](docs/CLI.md) | command, output, and exit-code contract |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, data flow, and trust boundaries |
| [docs/STATE_MODEL.md](docs/STATE_MODEL.md) | snapshot and export state transitions |
| [docs/WORKSPACE_FORMAT.md](docs/WORKSPACE_FORMAT.md) | private runtime artifact layout |
| [docs/SECURITY.md](docs/SECURITY.md) | detailed threat model and controls |
| [docs/CONFLICTS.md](docs/CONFLICTS.md) | `fail` and `replace-managed` semantics |
| [docs/WINDOWS_TESTING.md](docs/WINDOWS_TESTING.md) | live-validation runbook |
| [docs/FULL_EXPORT.md](docs/FULL_EXPORT.md) | complete export, backup gates, and validation |
| [docs/SNAPSHOT_FORMAT.md](docs/SNAPSHOT_FORMAT.md) | immutable snapshot format |
| [docs/ONENOTE_BACKUP.md](docs/ONENOTE_BACKUP.md) | backup discovery and version selection |
| [docs/ONENOTE_COM_KNOWN_ISSUE.md](docs/ONENOTE_COM_KNOWN_ISSUE.md) | native COM crash incident |
| [docs/ONENOTE_QUARANTINE.md](docs/ONENOTE_QUARANTINE.md) | exact-ID COM failure isolation |
| [docs/MATCHING.md](docs/MATCHING.md) | diagnostic matching algorithm |
| [examples/agent-session.md](examples/agent-session.md) | end-to-end command sequence |

Incomplete Graph/cloud work remains in [TODO.md](TODO.md).

## License

[MIT](LICENSE).
