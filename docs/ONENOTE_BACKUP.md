# Local OneNote Backup Source

The `backup` backend reads local OneNote `.one` section backups without using
the OneNote COM process. It is read-only. A complete, checksum-valid backup
snapshot is the currently supported source for deterministic full export; it
also provides a point-in-time baseline when COM `GetPageContent` crashes.
The observed native crash and the reason quarantine did not bypass it are
documented in [ONENOTE_COM_KNOWN_ISSUE.md](ONENOTE_COM_KNOWN_ISSUE.md).

## Automatic discovery

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend backup `
    --output artifacts\snapshots\source-backup
```

When `--backup-root` is omitted, `joplin-importer` searches below
`%LOCALAPPDATA%\Microsoft\OneNote`, considers numeric version directories, and
selects the child directory with the strongest set of recursive `.one` backup
files. The localized backup directory name is deliberately not hard-coded.

If automatic discovery is not appropriate, supply a root explicitly:

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend backup `
    --backup-root <backup-directory> `
    --output artifacts\snapshots\source-backup
```

## Version selection

OneNote can keep several dated files for the same logical section. `joplin-importer`
always selects exactly one: the file with the newest filesystem modification
time. A deterministic relative-filename tie-break is used only when times are
equal. Older versions are counted in the manifest as
`older_versions_skipped` and are never opened as fallback sources.

The dated filename suffix is removed only for logical grouping; parentheses
that are part of the actual section name are preserved.

## Provenance and limitations

The manifest stores `backup_selection=latest`, whether the root was found in
`auto` or `manual` mode, selected-file hashes, and only paths relative to the
backup root. It does not persist the absolute backup root.

Backup snapshots have the `corroborating` audit role because they are
point-in-time copies, but a complete snapshot may be selected explicitly for
full export. Page IDs are stable
synthetic identifiers derived from notebook/section placement, page order,
and title because the parser does not expose live COM page IDs. A section that
cannot be parsed is recorded in `errors.jsonl`; the snapshot becomes `partial`
instead of silently treating its pages as absent.

Aspose Note 26.3.2 incorrectly treats the `VersionProxy`
`VersionHistoryGraphSpaceContextNodes` property as a nested property set. The
file format stores a four-byte ContextID-array count there. The adapter applies
a narrow in-memory compatibility override while constructing the document and
restores the library setting immediately afterward. It never patches the
backup file; selected-file hashes are recorded before parsing.

The normalizer also removes OneNote's legacy `HYPERLINK` field marker from
visible text while preserving the label span and one link destination. This
prevents both the stray marker and duplicated Markdown URLs in exported notes.
