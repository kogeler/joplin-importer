# OneNote COM Page Quarantine

Use a quarantine only when a small, identified set of OneNote pages crashes
the native desktop process during `GetPageContent`. Quarantine is read-only:
it prevents those exact page IDs from being passed to `GetPageContent`; it
does not change OneNote or Joplin.

This is not a repair mechanism. In the documented real incident, the crashing
set kept growing and quarantine did not produce a complete COM snapshot. Read
[ONENOTE_COM_KNOWN_ISSUE.md](ONENOTE_COM_KNOWN_ISSUE.md) before using it.

Create the ignored local `artifacts/onenote-quarantine.json` file:

```json
{
  "schema_version": 1,
  "pages": [
    {
      "page_id": "{ONE-NOTE-PAGE-ID}",
      "expected_title": "Page title shown by GetHierarchy",
      "reason": "ONENOTE.EXE crash reproduced during GetPageContent"
    }
  ]
}
```

`page_id`, `expected_title`, and `reason` are required. Duplicate page IDs,
unknown fields, empty values, malformed JSON, and unsupported schema versions
are rejected before the snapshot staging directory is created. The entire
root `artifacts/` directory is Git-ignored because quarantine titles, reasons,
snapshots, and reports can contain private information.

Run the scan with either option name:

```powershell
.venv\Scripts\joplin-importer.exe scan-onenote --backend com `
    --quarantine-file artifacts\onenote-quarantine.json `
    --output artifacts\snapshots\source-com-quarantined

# Compatibility alias:
.venv\Scripts\joplin-importer.exe scan-onenote --backend com `
    --skip-page-file artifacts\onenote-quarantine.json `
    --output artifacts\snapshots\source-com-quarantined
```

## Result and review

A matched entry is skipped by exact page ID before any content call. If its
current title differs from `expected_title`, it is still skipped safely and
the mismatch is reported. An entry whose ID is absent from the current
hierarchy is reported as stale. Entries outside a selected `--notebook` or
recycle-bin scope are counted but do not make that scoped scan partial.

Any matched or stale quarantine entry makes the snapshot `partial`, so the
command returns exit code 1. Review:

* `errors.jsonl` in the snapshot for `IntentionallyQuarantined` and
  `StaleQuarantineEntry` records;
* `manifest.json` for quarantine counts, the source-file-independent
  quarantine digest, and coverage notes;
* `extractor-errors.csv` after `joplin-importer compare` for the same records in tabular
  form.

Quarantine cannot identify a bad page without attempting `GetPageContent`,
cannot repair its native OneNote representation, and cannot recover skipped
content. If new crashing IDs keep appearing, stop the experiment. During the
recorded incident an operator cap of 20 entries was set to avoid an unbounded
restart/skip cycle.

Do not interpret a quarantined page as missing from OneNote or Joplin. It was
intentionally not extracted. A quarantined snapshot is partial and cannot be
used as a full-export source.
Never quarantine pages merely to obtain a green scan, and do not quarantine
an entire notebook when ordinary pages also crash. Widespread crashes mean
the COM backend is unavailable; use a complete newest local backup. Microsoft
Graph is experimental analysis-only until [TODO.md](../TODO.md) is complete. If
the native OneNote process becomes unavailable, the scanner stops immediately
instead of incorrectly reporting every remaining page as another extraction
failure; the unattempted count is recorded in `manifest.json`.

Schema: [`schemas/onenote-quarantine.schema.json`](../schemas/onenote-quarantine.schema.json).

Known native failure:
[ONENOTE_COM_KNOWN_ISSUE.md](ONENOTE_COM_KNOWN_ISSUE.md).
