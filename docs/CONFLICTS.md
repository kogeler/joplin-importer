# Conflict and Ownership Rules

The complete exporter never merges individual pages. A conflict is a target
profile condition that prevents the whole plan from being applied safely.

## Policies

| Policy | Intended use | Existing managed export |
| --- | --- | --- |
| `fail` | first export into an empty/new destination | blocks the run |
| `replace-managed` | refresh one previous complete export set | replaces the entire old set |

`fail` is the default. `replace-managed` is not an overwrite switch for
ordinary notebooks.

## Conditions that always block

- An unmanaged top-level notebook has a planned title or a reserved staging
  title.
- A staging export from another plan exists.
- Managed active roots belong to more than one old export-set ID.
- A root or child marker is missing, malformed, duplicated, or inconsistent.
- The plan fingerprint identifies another Joplin profile.
- Live preconditions changed after the successful dry-run receipt.

These conditions are not resolved through fuzzy matching, title guessing, or
partial export. Inspect the target profile, preserve evidence, and build a new
plan/approval/dry-run chain after resolving the external condition.

## Managed ownership

Every exported folder carries structured `joplin_importer` ownership data in
Joplin `user_data`. Root markers include the export-set/plan identity, source
node identity, and state (`staging` or `active`). Notes carry deterministic
plan/action markers and an encoded OneNote page ID; resources use a
content-hash title marker.

Readers also recognize the legacy marker namespace written before the project
rename. New exports always write the `joplin-importer` namespace.

Title equality alone never establishes ownership. Missing ownership metadata
makes an object unmanaged even when its title is identical to a planned root.

## Replacement order

1. Prove that all old active roots belong to one complete managed set.
2. Create the entire new tree under temporary root titles.
3. Re-read and verify every planned folder and note.
4. Promote all new roots to their final titles and active state.
5. Re-read the promoted set.
6. Move every root in the old managed set to normal Joplin trash.

The old set is not touched before the new set is complete and visible. The
exporter never requests permanent deletion and never mutates unmanaged data.
`replace-managed` requires the explicit `--confirm-full-replace` flag.

See [FULL_EXPORT.md](FULL_EXPORT.md) for the complete operator workflow and
[STATE_MODEL.md](STATE_MODEL.md) for artifact/state transitions.
