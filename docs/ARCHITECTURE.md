# Architecture

## Module map

| Module | Responsibility |
| --- | --- |
| `models/` | versioned records, deterministic hashing, staged snapshot writer/reader |
| `transport.py` | guarded Joplin HTTP, retry policy, redacted request ledger |
| `secretstore.py` | token-file/environment loading and redaction |
| `adapters/onenote_backup/` | localized discovery, latest-only selection, read-only parsing |
| `adapters/onenote_com/` | Windows hierarchy/content scan and exact-ID quarantine |
| `adapters/onenote_graph/` | experimental delegated read-only Graph scan |
| `adapters/joplin/` | paginated inventory and exporter write primitives |
| `normalization/` | format-native XML/HTML/Markdown to one semantic model |
| `matching/` | deterministic rules, scored candidates, global assignment, findings |
| `reporting/` | offline HTML, JSON, and CSV audit output |
| `exporting/` | complete plan, approval, dry-run, staged apply, validation |
| `repair/` | unsupported legacy partial-repair internals; not in public CLI |
| `cli/` | Click commands, output selection, and exit-code mapping |

## Data flow

```text
OneNote backup / COM / experimental Graph       Joplin Data API
                    |                                  |
                    +----------- snapshots -----------+
                                    |
                 +------------------+------------------+
                 |                                     |
          diagnostic analysis                  complete export
      normalize -> match -> detect       plan -> dry-run -> apply
                 |                                     |
       HTML / JSON / CSV reports          validate managed tree
             (read-only)                    (no page merge)
```

The analysis and export paths share snapshot/normalization models but not
decisions. Audit matches cannot become export operations. The exporter walks
every page in one complete source snapshot and builds its own deterministic
folder/note specification.

## Snapshot boundary

All scanners write through `SnapshotWriter` to `<output>.staging`. Finalize
computes the canonical inventory hash, builds the derived SQLite index,
records checksums, and atomically publishes the directory. `SnapshotReader`
accepts finalized snapshots only and bounds every relative read to the
snapshot root.

Source adapters record backend, coverage, limitations, and relative
provenance. A complete local backup snapshot is the supported full-export
source. COM is analysis evidence; quarantined COM output is partial. Graph is
experimental corroborating evidence and is rejected by export planning.

## Read-only analysis

Normalization preserves format-specific raw hashes while producing a shared
semantic model for cross-format comparison. The matching pipeline applies
unique deterministic rules, scored features, and per-component Hungarian
assignment. Detection turns the result into evidence/cause-classified
findings. Reports explain states; they never prescribe a mutation.

Details of the algorithm are in [MATCHING.md](MATCHING.md).

## Complete export

```text
complete snapshot -> deterministic plan/body bundle -> digest approval
 -> READ_ONLY live compiler + receipt -> immediate equivalent preflight
 -> stage all objects -> verify -> promote -> trash old managed set
 -> fresh Joplin snapshot -> strict validation
```

All Joplin traffic uses `HttpTransport`. Dry-run constructs predicted
operations while the transport itself rejects mutating methods. Apply switches
the transport to write-enabled mode only after artifact, backup/waiver, sync,
fingerprint, ownership, and live-precondition checks pass.

Folders carry structured ownership markers. Notes carry deterministic
plan/action markers plus their encoded OneNote page ID. Resources are resolved
by SHA-256 marker. These identities make retries and strict validation
independent of titles.

## Key decisions

1. **No partial merge.** Historical matching can be uncertain and therefore
   cannot safely drive writes. Export is complete and source-derived.
2. **Read-only by construction.** A CLI promise is insufficient; the shared
   transport blocks mutating methods before network I/O.
3. **Immutable evidence.** Snapshots, plans, approvals, and receipts are
   versioned and hash-bound so later stages can detect changes.
4. **Stage before replacement.** The entire new tree is re-read before any old
   managed root moves to trash. Unmanaged roots are never touched.
5. **Content-aware comparison.** Raw OneNote XML/HTML and Joplin Markdown are
   never compared as equivalent byte formats; a versioned semantic normalizer
   supplies the cross-format representation.
6. **Explicit incomplete coverage.** Extraction failures remain in
   `errors.jsonl`; partial coverage is useful for analysis but categorically
   rejected for export.

State transitions are documented in [STATE_MODEL.md](STATE_MODEL.md), and the
security boundary in [SECURITY.md](SECURITY.md).
