# Snapshot Format

Every scan produces one immutable snapshot directory. Writes go to
`<name>.staging`; `finalize()` builds the inventory, computes checksums, and
publishes the snapshot. It normally uses an atomic rename; when a desktop sync
client keeps the directory locked, it performs a verified copy and publishes
the manifest last as the commit marker. Resume an interrupted scan with
`--resume` (already-staged records are skipped); never modify a finalized
snapshot.

```
artifacts/snapshots/2026-07-16T120000Z-onenote-com/
  manifest.json        # schema/tool/adapter versions, counts, coverage,
                       # deterministic inventory_hash, per-file sha256
  scan-metadata.json   # volatile info: timings, host OS, limitations
  inventory.sqlite     # convenience index (records table); derived data
  errors.jsonl         # one ErrorRecord per failed item (volatile)
  pages/
    <sha256(record-id)[:32]>.record.json    # PageRecord / NoteRecord
    <...>.raw.xml|.md|.html                 # exact raw content
    <...>.semantic.json                     # canonical semantic model
  resources/
    <sha256><ext>                           # content-addressed binaries
```

Schemas: [`page-record`](../schemas/page-record.schema.json),
[`note-record`](../schemas/note-record.schema.json),
[`manifest`](../schemas/manifest.schema.json),
[`scan-metadata`](../schemas/scan-metadata.schema.json), and
[`error-record`](../schemas/error-record.schema.json).

## Determinism

`manifest.inventory_hash` is the SHA-256 of the sorted canonical-JSON record
list. Two scans of identical logical content produce identical hashes;
volatile metadata (timings, error logs, sqlite bytes) does not affect it.
Comparing the before/after `inventory_hash` of two Joplin scans is the
standard independent proof that `export-dry-run` mutated nothing.

## Record highlights

* Raw content keeps its format-specific hash (`raw_content_sha256`), which is
  never compared across formats.
* `semantic_model_sha256` + `normalizer_version` allow cross-format
  comparison; records normalized by an older normalizer are transparently
  re-normalized from raw content during `joplin-importer compare`.
* Resources are stored by content hash; missing or unreadable resources stay
  in the record with an explicit status instead of disappearing.
* Private note contents live only inside snapshots, which are git-ignored by
  default. A deterministic layout does not make a private snapshot safe to
  commit.
* Backup provenance contains only paths relative to the selected root. Real
  absolute machine paths must never be persisted or committed.
* Graph snapshots are experimental corroborating evidence only until the
  live work in [TODO.md](../TODO.md) is complete.
