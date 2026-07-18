# Contributing

## Ground rules

- Development and automated tests never contact live OneNote or Joplin.
- Only the Windows test machine may run COM, live Joplin, export dry-run,
  apply, and validation. Follow
  [docs/WINDOWS_TESTING.md](docs/WINDOWS_TESTING.md).
- Preserve the guarded transport, complete-source, approval, backup,
  ownership, and post-write verification invariants in [AGENTS.md](AGENTS.md).
- Persisted snapshot/export formats and CLI exit codes are public contracts;
  incompatible changes require a migration and an appropriate SemVer bump.
- Microsoft Graph remains experimental and fake-server-tested only.

## Development setup

CPython 3.14 is required. The Makefile owns the project `.venv`, and CI runs
the same targets:

```sh
make venv
make check
make package
make verify-release
make smoke
make help
```

For isolated Linux validation with the official Python 3.14 image:

```sh
make container-check
make container-release
```

Normal tests use fake OneNote, Graph, and Joplin implementations. Never use a
real `export-apply` to investigate a failure.

## Dependency policy

Runtime and development constraints are declared in `pyproject.toml`. The
complete common lock is `requirements-lock.txt`; the Windows-only COM package
is pinned separately in `requirements-windows-lock.txt`.

Refresh the common lock in the official Python 3.14 container:

```sh
make container-freeze
```

Commit the relevant lock files whenever constraints change. Do not install
project dependencies globally.

## Versioning

The root `.version` file is the single version source. Setuptools reads it
dynamically, runtime `joplin_importer.__version__` resolves it from the
checkout or installed distribution metadata, and `make verify-release`
checks the result.

Every non-Dependabot pull request must increment `.version` with a plain
`X.Y.Z` SemVer value and update [CHANGELOG.md](CHANGELOG.md). The version CI
job requires the new value to be strictly greater than the pull-request base.
Dependabot lock and workflow maintenance does not publish a new application
release by itself.

## Pull requests

- Keep one logical change per pull request.
- Add focused tests for observable behavior.
- Run `make schemas` and commit generated schemas after persisted model
  changes.
- `make check`, `make package`, `make verify-release`, and `make smoke` must
  pass before merge.
- Do not add a Git remote, push, or create a release tag without explicit
  authorization.

## Release process

See [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md).
