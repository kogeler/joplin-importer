# Security Notes

Policy and reporting instructions are in the repository-root
[SECURITY.md](../SECURITY.md).

## Threat model

Protected assets are the Joplin token, private notebook content and metadata,
the integrity of the live Joplin profile, and filesystem data outside the
selected artifact roots. Untrusted inputs include OneNote XML/binary backups,
Joplin HTML/Markdown and titles, Graph responses, resource filenames, and
persisted snapshots or export artifacts supplied to a later command.

## Controls

### Secrets and network

- Tokens are loaded only from `--token-file` or a named `--token-env` variable
  into a non-printing secret wrapper.
- URL, exception, retry, ledger, report, and receipt paths redact token values.
- All Joplin requests pass through `HttpTransport`. In `READ_ONLY` mode,
  `POST`, `PUT`, `PATCH`, and `DELETE` are rejected before network I/O.
- The default endpoint is loopback Joplin Web Clipper. There is no telemetry.
- The experimental Graph adapter requests delegated `Notes.Read` access and
  stores no client secret. Its real authentication and coverage remain
  unvalidated and are not an accepted recovery boundary.

### Content and filesystem

- XML, HTML, and Markdown are parsed as data; source scripts, styles, event
  handlers, and unsafe URLs do not enter generated content or reports.
- Snapshot-relative reads reject absolute paths and traversal outside the
  snapshot root.
- Backup provenance persists paths relative to the selected backup root, not
  absolute machine paths.
- Snapshots finalize through a staging directory, deterministic checksums, and
  an atomic rename. Export planning rechecks every source checksum.
- Resources are content-addressed and their hashes are verified throughout
  planning and validation.

### Mutation and ownership

1. Analysis, scans, `doctor`, and export dry-run are structurally read-only.
2. Approval is bound to the exact plan digest; the receipt is bound to the
   plan, approval, target profile, and live preconditions.
3. Apply reruns the read-only compiler immediately before enabling the planned
   mutations.
4. `fail` rejects existing managed exports and title collisions.
5. `replace-managed` recognizes only a complete, unambiguous old managed set.
   It stages and verifies the entire new set before moving old roots to normal
   Joplin trash.
6. Permanent deletion is never requested. Unmanaged notebooks are never
   changed or claimed by title.
7. A current JEX and sync confirmation are required unless one narrow,
   immediately re-proven waiver from [FULL_EXPORT.md](FULL_EXPORT.md) applies.

### Private artifacts

Snapshots, raw note bodies, resources, reports, plans, approvals, receipts,
ledgers, operation records, JEX files, and quarantine files may all contain
private information. Conventional paths are ignored by Git, but ignore rules
are not an access-control mechanism. Keep artifacts on trusted storage and
review `git status` before commits.

## Residual risks

- Anyone with local access to the Joplin Clipper endpoint and token can access
  the profile within Joplin's own trust boundary.
- A bug or native crash in OneNote/Aspose/COM can produce partial extraction;
  completeness and checksum gates prevent that snapshot from becoming an
  export source but cannot recover the missing data.
- Joplin has no transaction spanning a complete tree. Staging and verification
  preserve the old set until promotion, but interruption after promotion may
  temporarily leave both sets visible until the same plan is resumed.
- Reports and receipts redact secrets, not notebook titles or content-derived
  identifiers. They remain private recovery evidence.
- The full export intentionally mutates live Joplin. Operator selection of the
  wrong profile is mitigated by the fingerprint and immediate preflight, not
  made impossible.
