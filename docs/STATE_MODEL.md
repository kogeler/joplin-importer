# State and Artifact Model

`joplin-importer` has no continuously synchronized workspace or mutable base
database. State is carried by immutable snapshots and a digest-bound sequence
of export artifacts.

## Snapshot lifecycle

```text
absent -> <snapshot>.staging -> finalized snapshot
                    \-> resume with --resume
```

A scanner writes records, raw content, semantic models, resources, and errors
to a staging directory. Finalization builds the inventory, computes the
deterministic inventory hash and per-file checksums, writes `manifest.json`,
and atomically renames the directory. Finalized snapshots are read-only.

Coverage controls what may happen next:

| Coverage | Analysis | Complete export |
| --- | --- | --- |
| `complete` | allowed | allowed after checksum verification |
| `partial` | allowed with explicit extractor evidence | rejected |

## Analysis flow

```text
source snapshot + target snapshot
        -> normalize/re-normalize
        -> deterministic and scored matching
        -> findings with evidence/cause classes
        -> HTML / JSON / CSV reports
```

Matching output is diagnostic state only. It cannot be consumed by
`export-plan` and never selects a page to write, merge, or delete.

## Export artifact chain

```text
complete snapshot
    -> plan + body bundle
    -> approval(plan SHA-256)
    -> dry-run receipt(plan + approval + profile + live preconditions)
    -> apply receipt(plan + approval + dry-run receipt + backup proof)
    -> post-export snapshot
    -> validation report
```

Each transition reloads and validates its inputs. Editing a plan invalidates
the approval; editing an approval invalidates the receipt; changing live
Joplin state invalidates the receipt's precondition fingerprint. The plan ID
is deterministic for the source inventory, complete folder/note specification,
content mode, and conflict policy.

## Target state transitions

```text
preflight-safe
    -> create complete staging tree
    -> verify staging tree
    -> promote every new root to active
    -> verify active tree
    -> trash previous managed roots (replace-managed only)
    -> validate from a new immutable snapshot
```

Before promotion, failure leaves the old managed set untouched. A failure
after promotion can temporarily leave both old and new managed sets visible;
rerunning the same approved plan recognizes already-created deterministic
objects and completes the remaining work. An already-complete plan is a
successful no-op.

## Preconditions

Apply requires all of the following:

- the source snapshot ID/manifest hash and file checksums still match;
- the approval digest matches the plan;
- the successful receipt matches the plan, approval, and target fingerprint;
- an immediate read-only compiler produces the same live precondition
  fingerprint as dry-run;
- the conflict policy and ownership state are valid;
- sync is operator-confirmed complete;
- a current JEX backup exists, or one documented live-proven waiver applies.

## Validation state

Validation locates managed notes by embedded source OneNote page ID. It checks
uniqueness, structure, semantic content, and resource hashes. Strict mode also
compares total active profile counts with the plan. The export is accepted
only when the validation report result is `ok` with no issues.
