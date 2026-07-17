# Security Policy

## Reporting

Send suspected vulnerabilities privately to the repository owner. Do not open
a public issue. Use synthetic fixtures and never include real notebook
content, tokens, account IDs, snapshot artifacts, or local paths.

## Security model

- Tokens are accepted only through `--token-file` or `--token-env`; they are
  redacted from URLs, logs, errors, ledgers, reports, and receipts.
- All Joplin traffic uses one guarded transport. Read-only commands and
  `export-dry-run` reject mutation before network I/O.
- Full export operates only on complete, checksum-valid snapshots and
  digest-bound artifacts. It fails on unmanaged conflicts and never requests
  permanent deletion.
- Source content, snapshot artifacts, and reports are private data. Their
  standard workspace locations are ignored by Git.
- The experimental Graph scanner requests delegated read access only and is
  not accepted as a production restore source.

See [docs/SECURITY.md](docs/SECURITY.md) for the detailed threat model,
controls, and residual risks.
