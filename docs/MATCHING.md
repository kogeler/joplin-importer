# Diagnostic Matching

Matching is a read-only diagnostic facility for comparing snapshots, including
historical broken imports. Its results never feed a public repair/merge command
and never authorize Joplin changes. Use the deterministic full-export workflow
when data must be restored.

The original import did not preserve OneNote page IDs, so matching is
multi-stage. Match confidence (identity) is separate from content integrity:
an `exact` match may still have an empty body — that becomes an `empty-body`
finding, not a lower match confidence.

## Stage A — deterministic

Applied in order; each rule requires a unique, unambiguous candidate:

1. `deterministic:embedded-source-id` — a note stores the OneNote page ID in
   `source_url` (`onenote://page/...`) or a `joplin-importer:onenote_page_id` body
   marker → `exact`.
2. `deterministic:path-title-time` — identical notebook/section path,
   identical normalized title (placeholder titles excluded), creation times
   within 120 s → `exact`.
3. `deterministic:semantic-hash` — identical canonical semantic hash from the
   same normalizer version, unique on both sides, non-empty content →
   `exact`.
4. `deterministic:text-hash-in-section` — identical visible-text hash within
   the same section path, unique, ≥ 20 chars → `high-confidence`.

Raw HTML/Markdown hash equality is never used for cross-format matching.

## Stage B — scored

Remaining pairs get a weighted score over features (each in [0,1] or
inapplicable): title similarity (placeholder titles carry no evidence), path,
creation/modification time proximity, text similarity
(truncation-tolerant containment), semantic-model equality, resource-hash
overlap, image/attachment counts, URL overlap, distinctive text fragments,
page level. Weights and thresholds live in
`matching/scoring.py` (`MatchingConfig`, version `joplin-importer-thresholds/1`) and were
calibrated against the labeled synthetic fixtures in
`tests/unit/test_matching.py`. Every result stores its features, weights,
score, and runner-up margin.

## Stage C — assignment

Candidates form a bipartite graph solved per connected component with the
Hungarian algorithm (`matching/assignment.py`, verified against brute force).
Every node has an explicit unmatched option worth `min_score / 2`, so a pair
is selected only when its score beats leaving both endpoints unmatched —
duplicate `Untitled Page` items are never force-paired. Confidence buckets:
`high-confidence` ≥ 0.82, `probable` ≥ 0.65 (both need margin ≥ 0.05 over the
runner-up), `ambiguous` ≥ 0.45, otherwise `unmatched`.

## Detection

`matching/detection.py` turns matches into findings, each with an evidence
class (`confirmed`/`probable`/`uncertain`/`informational`) and a cause class
(`migration-loss`/`source-drift`/`format-conversion`/`extractor-failure`/
`target-extra`/`unknown`). The import window is estimated from the cluster of
note `created_time` values; pages edited after it soften content findings to
`source-drift`/`uncertain` instead of asserting migration loss.
