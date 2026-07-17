# TODO — complete and live-test Microsoft Graph cloud support

## Current status

The Microsoft Graph backend is **unfinished and not production-supported**.
What exists today is a read-only snapshot scanner with automated tests against
a fake Graph server. It is not bidirectional synchronization, it does not write
to OneNote, and a real cloud-to-Joplin recovery has not been completed or
validated.

In particular, no accepted live run has yet proven:

* device-code authentication against a real Microsoft account/tenant;
* complete enumeration of real notebooks, nested section groups, sections,
  pages, and all pagination paths;
* reliable retrieval of real page HTML, images, attachments, and large
  resources;
* behavior for shared notebooks, deleted/recycle-bin content, conflicts,
  unavailable notebooks, tenant policies, throttling, and expired consent;
* restart/resume behavior after live Graph failures;
* parity between Graph, a complete local backup, and OneNote COM;
* a complete Graph-source export, dry-run, apply, and strict Joplin validation.

Until all items below are complete, Graph snapshots are corroborating analysis
only. They must not be presented as authoritative backups or used as the sole
source for `export-plan`.

## Required implementation work

- [ ] Define the exact supported Microsoft account, tenant, shared-notebook,
      and permission scopes; document known Graph OneNote API omissions.
- [x] Reject Graph snapshots in `export-plan` so the experimental backend
      cannot accidentally enter the supported export path.
- [ ] Implement secure token-cache behavior, logout/account switching, consent
      diagnostics, and redaction tests for real authentication errors.
- [ ] Verify and, where needed, fix pagination for every hierarchy and resource
      endpoint against a real tenant.
- [ ] Verify page HTML/resource discovery, content types, filenames, duplicate
      resource references, embedded objects, large payloads, and retry limits.
- [ ] Record enough tenant/account and API limitation metadata to distinguish
      an incomplete scan from a genuinely absent object without exposing
      personal identifiers.
- [ ] Add robust live resume checkpoints and per-object error isolation.
- [ ] Compare the same real notebook through Graph, COM, and the newest local
      backup; explain every coverage difference.
- [ ] Decide whether Graph can ever qualify as a complete export source. If it
      can, define strict completeness criteria and enforce them in
      `export-plan`; otherwise keep it permanently analysis-only.

## Required validation work

- [ ] Add opt-in `live_graph` tests that are excluded by default and never log
      tokens or notebook bodies.
- [ ] Add a Windows/live-tenant runbook using ignored artifact directories and
      synthetic or disposable notebooks where possible.
- [ ] Exercise pagination, throttling (`Retry-After`), token expiration,
      missing permissions, one-page failures, interrupted scans, and resume.
- [ ] Validate counts, hierarchy, normalized semantic content, links, images,
      attachments, and resource hashes against COM/backup baselines.
- [ ] If Graph becomes an export source, run the complete sequence on a clean
      Joplin profile: plan, approval, no-mutation dry-run with before/after
      inventories, apply, new scan, and strict validation.
- [ ] Record a release-level acceptance report without real account IDs, local
      paths, tokens, or private notebook content.

Cloud support may be called complete only after the real tests above pass and
the documentation no longer needs an experimental warning.
