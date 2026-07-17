# joplin-importer - Agent Runbook

Read-only OneNote/Joplin snapshot analysis and deterministic complete
backup-to-Joplin export. Partial merge/repair is unsupported. The root
`.version` file is the single version source.

## Requirements

- CPython **>= 3.14** on Windows or Linux.
- Podman for isolated local Linux validation.
- Joplin Desktop with Web Clipper enabled for live target operations.
- A complete local OneNote backup for the supported export source.
- Windows and OneNote Desktop only for the optional COM scanner.

Development machines run implementation and automated fake-server tests only.
COM, live Joplin, export dry-run, apply, and validation run only on the Windows
test machine, following [docs/WINDOWS_TESTING.md](docs/WINDOWS_TESTING.md).

Microsoft Graph is experimental and fake-server-tested only. Do not use it as
a complete restore source until [TODO.md](TODO.md) is finished.

## Working from a checkout

Always use the Makefile-managed project virtual environment; never install
dependencies globally. CI calls the same targets:

```sh
make venv
make check
make package
make verify-release
make smoke
make container-check  # official Python 3.14 image in Podman
make help
```

Run `make schemas` after persisted model changes. When common dependency
constraints change, refresh `requirements-lock.txt` with
`make container-freeze`; keep the Windows-only `pywin32` pin in
`requirements-windows-lock.txt` separate.

## Authentication

| Source | Option |
| --- | --- |
| ignored local file | `--token-file PATH` |
| named environment variable | `--token-env NAME` |
| Joplin endpoint | `--base-url URL` (default `http://127.0.0.1:41184`) |

There is no raw token argument. Never echo a token, place it in a command
argument, log it, or commit it. The token object is redacted from URLs,
exceptions, request ledgers, reports, and receipts.

## Canonical workflows

Analysis is read-only:

```text
doctor -> scan-onenote --backend backup|com / scan-joplin -> compare
```

Complete restore is no-merge:

```text
export-plan -> export-approve -> export-dry-run -> export-apply
            -> scan-joplin -> export-validate
```

The names `plan`, `approve`, `dry-run`, and `apply` belong to an unsupported
partial-repair prototype and must not return to the public command group. The
`repair/` package and its schemas remain internal only for historical artifact
analysis, debugging, and regression coverage.

## Command table

| Command | Purpose | Mutates live data |
| --- | --- | --- |
| `doctor` | probe Joplin and report the export fingerprint | no |
| `scan-onenote` | create a backup, COM, or experimental Graph snapshot | no |
| `scan-joplin` | create a Joplin snapshot through the Data API | no |
| `compare` | write diagnostic HTML/JSON/CSV reports | no |
| `export-plan` | compile a complete immutable export plan | no |
| `export-approve` | bind operator approval to the plan digest | no |
| `export-dry-run` | prove live preconditions and write a receipt/ledger | no |
| `export-apply` | stage, verify, and promote the complete managed tree | **Joplin** |
| `export-validate` | compare the plan/source with a post-export snapshot | no |

All public commands support `--json`, `--verbose`, and `--quiet`. See
[docs/CLI.md](docs/CLI.md) for arguments and output contracts.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | command succeeded; no reportable findings where relevant |
| `1` | partial scan, audit findings, unsafe dry-run, or validation issues |
| `2` | invalid command line, option combination, or artifact input |
| `3` | connectivity, extraction, execution, or other operational failure |

## Safety prohibitions

1. All Joplin traffic goes through `transport.HttpTransport`; `READ_ONLY`
   mode rejects `POST`, `PUT`, `PATCH`, and `DELETE` before network I/O.
2. Never use real `export-apply` to debug. Reproduce with fake servers and
   `export-dry-run` first.
3. Export accepts only a complete, checksum-valid source snapshot, a
   digest-bound approval, a successful same-profile dry-run receipt, and an
   immediate live preflight.
4. `fail` is the default conflict policy. `replace-managed` may replace only
   the complete previous `joplin-importer` export set, never unmanaged
   notebooks. See [docs/CONFLICTS.md](docs/CONFLICTS.md).
5. New trees are staged and verified before promotion. Old managed roots move
   only to Joplin trash after the new set is promoted; permanent deletion is
   forbidden.
6. A JEX backup and sync confirmation are normally required. Only the narrow,
   live-proven waivers in [docs/FULL_EXPORT.md](docs/FULL_EXPORT.md) are
   allowed.
7. Keep `token`, `.venv/`, snapshots, reports, plans, approvals, receipts,
   ledgers, journals, and JEX files ignored.
8. Never edit OneNote, its live cache, Joplin's database/profile, or finalized
   snapshots directly.
9. Do not add a Git remote or push without explicit authorization. Commit
   messages contain no AI attribution trailers.

## Partial scans

A snapshot with `coverage_status: "partial"` has entries in `errors.jsonl`.
`compare` copies them to `extractor-errors.csv`. A partial source cannot be
used for complete export. Resume interrupted scans with `--resume`.

OneNote Desktop is known to terminate or disconnect COM during
`GetPageContent` for a growing set of pages on the affected notebook.
Quarantine skips exact IDs but cannot make a complete source. Do not exceed
the documented 20-entry operator cap or keep restarting OneNote to grow the
list; use the newest complete backup. See
[docs/ONENOTE_COM_KNOWN_ISSUE.md](docs/ONENOTE_COM_KNOWN_ISSUE.md).

## Live evidence

On Windows, take a Joplin snapshot before and after `export-dry-run`; their
inventory hashes must match. The receipt must be `ok`, the mutation count
zero, and the transport ledger GET-only. After apply, run strict export
validation. Keep all evidence under ignored local paths.

A full command sequence is in
[examples/agent-session.md](examples/agent-session.md).

## Code map

| Concern | Location |
| --- | --- |
| Models, snapshots, hashing | `src/joplin_importer/models/` |
| Secrets, HTTP transport | `secretstore.py`, `transport.py` |
| Joplin adapter | `adapters/joplin/` |
| OneNote backup adapter | `adapters/onenote_backup/` |
| OneNote COM adapter | `adapters/onenote_com/` |
| Experimental Graph adapter | `adapters/onenote_graph/` |
| Normalization | `normalization/` |
| Diagnostic matching/detection | `matching/` |
| Reports | `reporting/` |
| Supported complete export | `exporting/` |
| Unsupported legacy repair internals | `repair/` |
| CLI | `cli/main.py` |

## Version and release

Every pull request increments `.version` and updates `CHANGELOG.md`.
`pyproject.toml` and runtime `__version__` resolve it dynamically. Pushes to
`main` are tagged and released only by `.github/workflows/release.yml`; do not
create release tags manually. See
[docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md).
