# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
semantic versioning.

## [Unreleased]

## [0.3.0] - 2026-07-18

### Added

* Self-contained AMD64 executables for Linux and Windows plus an ARM64 Linux
  executable, built with PyInstaller. CI launches each binary after building
  it, and releases publish only the platform artifacts that passed their
  native smoke tests.

## [0.2.1] - 2026-07-18

### Fixed

* Windows CI now verifies the COM dependency in the project virtual
  environment and parses wheel metadata independently of line-ending style.
* Dependabot and release automation now handle dependency-only maintenance
  without invalid transitive updates or duplicate application releases.

## [0.2.0] - 2026-07-18

### Added

* Documentation of the supported read-only analysis and deterministic
  backup-to-Joplin full-export workflows, including backup auto-discovery,
  latest-only section selection, COM quarantine, conflict handling,
  whole-managed-set replacement, link normalization, and validation.
* A known-issue report for the native OneNote `GetPageContent` crashes,
  unsuccessful Office repair/re-download attempts, and why a growing page
  quarantine did not provide complete coverage.
* `TODO.md` recording that Microsoft Graph cloud scanning/recovery is
  incomplete and has not been genuinely live/end-to-end tested.
* Makefile targets for pinned setup, linting, type checks, tests, schemas,
  packaging, artifact verification, smoke installs, and Podman validation.
* Linux/Windows GitHub Actions CI, mandatory pull-request version increments,
  automated tagged GitHub releases, checksums, and Dependabot configuration.
* Release and contribution runbooks.
* Canonical CLI, architecture, state, workspace, conflict, and detailed
  security references, plus a reproducible agent session example.

### Changed

* The supported full exporter no longer imports the legacy repair package.
* The project, Python package, and CLI are renamed to `joplin-importer`.
* Python 3.14 is now the minimum and CI-tested runtime, with a separate pinned
  Windows lock for the COM dependency.
* `.version` is now the single source for package and runtime versions. Managed
  exports use the new project namespace while remaining able to recognize
  ownership metadata written by earlier releases.
* Documentation now follows the same root/runbook/reference/example structure
  and `docs/` filename convention as `joplin-md-sync`; redundant human/agent
  quickstarts were consolidated into `README.md` and `AGENTS.md`.
* Private runtime output now uses one canonical root `artifacts/` directory;
  `.gitignore` no longer relies on broad or legacy artifact-name patterns.

### Removed

* Public `plan`, `approve`, `dry-run`, and `apply` commands for the unfinished
  partial-repair/merge workflow. Its internals and schemas remain available
  only for historical analysis, debugging, and regression tests.

## [0.1.0] - 2026-07-17

Initial implementation.

### Added

* Read-only inventory scans: OneNote desktop COM (Windows), Microsoft Graph
  (corroborating), Joplin Data API — all writing immutable, checksummed,
  resumable snapshots.
* Format-aware normalization into a canonical semantic model shared by
  OneNote XML, HTML, and Markdown; cross-format comparison that never
  mistakes serialization differences for data loss.
* Multi-stage matching (deterministic rules, weighted scoring with full
  explanations, Hungarian global assignment with unmatched options) and
  detection rules with evidence and cause classification.
* Offline HTML audit report plus JSON and CSV exports.
* Repair workflow: immutable create-only repair plan, digest-bound approval
  files, read-only dry-run with receipts and a request ledger proving zero
  mutations, create-only apply with preconditions, idempotency keys,
  append-only journal, and post-create verification.
* Initial CLI commands for scans, comparison, planning, approval, dry-run, and
  apply.
* JSON Schemas for every persisted document, kept current by tests.
* Documentation for humans, coding agents, and the Windows live-validation
  runbook.
