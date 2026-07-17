"""Multi-stage matching engine.

Stage A: deterministic rules (embedded source ID, path+title+time, unique
semantic hash, unique visible-text hash within a section).
Stage B: weighted scoring of remaining pairs with full explanations.
Stage C: globally consistent assignment with explicit unmatched options and a
minimum margin over the runner-up.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ..models import MatchConfidence, NoteRecord, PageRecord
from ..models.timeutil import epoch_seconds
from . import features as f
from .assignment import assign_pairs
from .results import MatchResult
from .scoring import MatchingConfig, classify, weighted_score

_TIME_TOLERANCE_S = 120.0
_MIN_TEXT_HASH_LEN = 20  # visible-text hash matches below this are boilerplate
_BLOCK_LINE_MIN_LEN = 30
_MAX_CANDIDATES_PER_PAGE = 100
_TITLE_TOKEN_RE = re.compile(r"\w{3,}", re.UNICODE)


def match_records(
    pages: list[PageRecord],
    notes: list[NoteRecord],
    config: MatchingConfig | None = None,
) -> list[MatchResult]:
    """Match source pages to Joplin notes; returns one result per source page
    plus an unmatched result for every unpaired note (joplin side)."""
    config = config or MatchingConfig()
    results: list[MatchResult] = []
    matched_pages: set[str] = set()
    matched_notes: set[str] = set()

    def take(result: MatchResult) -> None:
        results.append(result)
        matched_pages.add(result.source_page_id)
        if result.joplin_note_id:
            matched_notes.add(result.joplin_note_id)

    # ---- Stage A -------------------------------------------------------------
    for result in _stage_a(pages, notes, config):
        take(result)

    # ---- Stage B + C ------------------------------------------------------------
    remaining_pages = [p for p in pages if p.source_page_id not in matched_pages]
    remaining_notes = [n for n in notes if n.joplin_note_id not in matched_notes]
    results.extend(_stage_bc(remaining_pages, remaining_notes, config))
    return results


# -- Stage A ---------------------------------------------------------------------


def _stage_a(
    pages: list[PageRecord], notes: list[NoteRecord], config: MatchingConfig
) -> list[MatchResult]:
    out: list[MatchResult] = []
    used_notes: set[str] = set()
    used_pages: set[str] = set()

    # A1: embedded OneNote source ID
    notes_by_embedded: dict[str, list[NoteRecord]] = defaultdict(list)
    for note in notes:
        if note.embedded_onenote_page_id:
            notes_by_embedded[note.embedded_onenote_page_id].append(note)
    for page in pages:
        candidates = notes_by_embedded.get(page.source_page_id, [])
        if len(candidates) == 1 and candidates[0].joplin_note_id not in used_notes:
            note = candidates[0]
            out.append(
                _result(
                    page,
                    note,
                    MatchConfidence.EXACT,
                    "deterministic:embedded-source-id",
                    config,
                    ["note stores the OneNote page ID"],
                )
            )
            used_pages.add(page.source_page_id)
            used_notes.add(note.joplin_note_id)

    # A2: exact path + exact normalized title + timestamps within tolerance
    notes_by_path_title: dict[tuple, list[NoteRecord]] = defaultdict(list)
    for note in notes:
        if note.joplin_note_id in used_notes:
            continue
        key = (f.target_path(note), f.normalized_title(note.title))
        notes_by_path_title[key].append(note)
    for page in pages:
        if page.source_page_id in used_pages:
            continue
        title = f.normalized_title(page.page_title)
        if f.is_placeholder_title(page.page_title):
            continue  # placeholder titles never match deterministically
        candidates = [
            n
            for n in notes_by_path_title.get((f.source_path(page), title), [])
            if n.joplin_note_id not in used_notes
            and _times_close(page.created_at, n.user_created_at)
        ]
        if len(candidates) == 1:
            note = candidates[0]
            out.append(
                _result(
                    page,
                    note,
                    MatchConfidence.EXACT,
                    "deterministic:path-title-time",
                    config,
                    ["identical path, normalized title, and creation time"],
                )
            )
            used_pages.add(page.source_page_id)
            used_notes.add(note.joplin_note_id)

    # A3: unique canonical semantic hash (same normalizer version, non-empty)
    out.extend(
        _unique_key_stage(
            pages,
            notes,
            used_pages,
            used_notes,
            key_page=lambda p: _semantic_key(p),
            key_note=lambda n: _semantic_key(n),
            stage="deterministic:semantic-hash",
            confidence=MatchConfidence.EXACT,
            explanation="unique identical canonical semantic hash",
            config=config,
        )
    )

    # A4: unique visible-text hash within the same section
    out.extend(
        _unique_key_stage(
            pages,
            notes,
            used_pages,
            used_notes,
            key_page=lambda p: _text_key(p, f.source_path(p)),
            key_note=lambda n: _text_key(n, f.target_path(n)),
            stage="deterministic:text-hash-in-section",
            confidence=MatchConfidence.HIGH_CONFIDENCE,
            explanation="unique identical visible-text hash within the same section path",
            config=config,
        )
    )

    # A5: a forensic source may not expose creation timestamps. A unique
    # path+title still identifies the imported note without pretending the
    # unavailable timestamp matched.
    out.extend(
        _unique_key_stage(
            pages,
            notes,
            used_pages,
            used_notes,
            key_page=_missing_time_path_title_key,
            key_note=_path_title_key,
            stage="deterministic:unique-path-title-without-source-time",
            confidence=MatchConfidence.HIGH_CONFIDENCE,
            explanation="unique identical path and normalized title; source timestamp unavailable",
            config=config,
        )
    )

    # A6: when placement was changed by import, a globally unique, non-
    # placeholder exact title is useful identity evidence. Keep it probable,
    # not exact, because title alone does not prove content equality.
    out.extend(
        _unique_key_stage(
            pages,
            notes,
            used_pages,
            used_notes,
            key_page=_missing_time_unique_title_key,
            key_note=_unique_title_key,
            stage="deterministic:unique-title-without-source-time",
            confidence=MatchConfidence.PROBABLE,
            explanation="globally unique identical normalized title; source timestamp unavailable",
            config=config,
        )
    )
    return out


def _semantic_key(record: PageRecord | NoteRecord):
    if not record.semantic_model_sha256 or not record.normalizer_version:
        return None
    if not record.normalized_text and not record.resource_hashes:
        return None  # empty content: hash equality proves nothing
    return (record.normalizer_version, record.semantic_model_sha256)


def _text_key(record: PageRecord | NoteRecord, path: tuple[str, ...]):
    if not record.normalized_text_sha256:
        return None
    if len(record.normalized_text) < _MIN_TEXT_HASH_LEN:
        return None
    return (path, record.normalized_text_sha256)


def _unique_key_stage(
    pages,
    notes,
    used_pages: set[str],
    used_notes: set[str],
    *,
    key_page,
    key_note,
    stage: str,
    confidence: MatchConfidence,
    explanation: str,
    config: MatchingConfig,
) -> list[MatchResult]:
    out: list[MatchResult] = []
    pages_by_key: dict = defaultdict(list)
    notes_by_key: dict = defaultdict(list)
    for page in pages:
        if page.source_page_id not in used_pages and (key := key_page(page)) is not None:
            pages_by_key[key].append(page)
    for note in notes:
        if note.joplin_note_id not in used_notes and (key := key_note(note)) is not None:
            notes_by_key[key].append(note)
    for key, key_pages in pages_by_key.items():
        key_notes = notes_by_key.get(key, [])
        if len(key_pages) == 1 and len(key_notes) == 1:
            page, note = key_pages[0], key_notes[0]
            out.append(_result(page, note, confidence, stage, config, [explanation]))
            used_pages.add(page.source_page_id)
            used_notes.add(note.joplin_note_id)
    return out


def _path_title_key(record: PageRecord | NoteRecord):
    title = record.page_title if isinstance(record, PageRecord) else record.title
    if f.is_placeholder_title(title):
        return None
    path = f.source_path(record) if isinstance(record, PageRecord) else f.target_path(record)
    return (path, f.normalized_title(title))


def _missing_time_path_title_key(page: PageRecord):
    if page.created_at is not None:
        return None
    return _path_title_key(page)


def _unique_title_key(record: PageRecord | NoteRecord):
    title = record.page_title if isinstance(record, PageRecord) else record.title
    normalized = f.normalized_title(title)
    if f.is_placeholder_title(title) or len(normalized) < 4:
        return None
    return normalized


def _missing_time_unique_title_key(page: PageRecord):
    if page.created_at is not None:
        return None
    return _unique_title_key(page)


def _times_close(a_iso: str | None, b_iso: str | None) -> bool:
    a, b = epoch_seconds(a_iso), epoch_seconds(b_iso)
    if a is None or b is None:
        return False
    return abs(a - b) <= _TIME_TOLERANCE_S


# -- Stages B and C -----------------------------------------------------------------


def _stage_bc(
    pages: list[PageRecord], notes: list[NoteRecord], config: MatchingConfig
) -> list[MatchResult]:
    scores: dict[tuple[str, str], float] = {}
    details: dict[tuple[str, str], tuple[dict, int]] = {}
    candidate_index = _CandidateIndex.build(notes)
    for page in pages:
        for note in candidate_index.candidates(page):
            feats = f.compute_features(page, note)
            score, applicable = weighted_score(feats, config.weights)
            if score >= config.min_score and applicable >= config.min_applicable_features:
                key = (page.source_page_id, note.joplin_note_id)
                scores[key] = score
                details[key] = (feats, applicable)

    assigned = assign_pairs(scores, unmatched_baseline=config.min_score)

    # runner-up margin per source page (best alternative among its candidates)
    best_scores: dict[str, list[float]] = defaultdict(list)
    for (page_id, _note_id), score in scores.items():
        best_scores[page_id].append(score)

    notes_by_id = {n.joplin_note_id: n for n in notes}
    out: list[MatchResult] = []
    paired_notes: set[str] = set()

    for page in pages:
        note_id = assigned.get(page.source_page_id)
        if note_id is None:
            out.append(_unmatched_page(page, config))
            continue
        key = (page.source_page_id, note_id)
        score = scores[key]
        feats, applicable = details[key]
        others = sorted(best_scores[page.source_page_id], reverse=True)
        margin = score - others[1] if len(others) > 1 else 1.0
        confidence = classify(score, margin, applicable, config)
        if confidence is MatchConfidence.UNMATCHED:
            out.append(_unmatched_page(page, config))
            continue
        paired_notes.add(note_id)
        note = notes_by_id[note_id]
        out.append(
            _result(
                page,
                note,
                confidence,
                "scored:global-assignment",
                config,
                _explain(feats, score, margin),
                score=score,
                margin=margin,
                features=feats,
            )
        )

    for note in notes:
        if note.joplin_note_id not in paired_notes:
            out.append(
                MatchResult(
                    source_page_id="",
                    joplin_note_id=note.joplin_note_id,
                    confidence=MatchConfidence.UNMATCHED,
                    stage="unmatched:target",
                    threshold_version=config.version,
                    explanation=["no source page paired with this note"],
                    target_title=note.title,
                    target_path=list(f.target_path(note)),
                )
            )
    return out


@dataclass(slots=True)
class _CandidateIndex:
    """Cheap inverted indexes used before expensive pairwise text scoring."""

    notes_by_id: dict[str, NoteRecord]
    by_title: dict[str, set[str]]
    by_section: dict[str, set[str]]
    by_text_hash: dict[str, set[str]]
    by_resource: dict[str, set[str]]
    by_url: dict[str, set[str]]
    by_line: dict[str, set[str]]
    by_title_token: dict[str, set[str]]
    by_path_part: dict[str, set[str]]

    @classmethod
    def build(cls, notes: list[NoteRecord]) -> _CandidateIndex:
        indexes: dict[str, defaultdict[str, set[str]]] = {
            name: defaultdict(set)
            for name in (
                "title",
                "section",
                "text_hash",
                "resource",
                "url",
                "line",
                "title_token",
                "path_part",
            )
        }
        notes_by_id = {note.joplin_note_id: note for note in notes}
        for note in notes:
            note_id = note.joplin_note_id
            title = f.normalized_title(note.title)
            if not f.is_placeholder_title(note.title):
                indexes["title"][title].add(note_id)
            for token in _title_tokens(title):
                indexes["title_token"][token].add(note_id)
            if note.notebook_path:
                indexes["section"][note.notebook_path[-1].casefold()].add(note_id)
            for part in note.notebook_path:
                indexes["path_part"][part.casefold()].add(note_id)
            if note.normalized_text_sha256:
                indexes["text_hash"][note.normalized_text_sha256].add(note_id)
            for digest in note.resource_hashes:
                indexes["resource"][digest].add(note_id)
            for url in note.link_targets:
                if not url.startswith(":/"):
                    indexes["url"][url].add(note_id)
            for line in _blocking_lines(note.normalized_text):
                indexes["line"][line].add(note_id)
        return cls(
            notes_by_id=notes_by_id,
            by_title=dict(indexes["title"]),
            by_section=dict(indexes["section"]),
            by_text_hash=dict(indexes["text_hash"]),
            by_resource=dict(indexes["resource"]),
            by_url=dict(indexes["url"]),
            by_line=dict(indexes["line"]),
            by_title_token=dict(indexes["title_token"]),
            by_path_part=dict(indexes["path_part"]),
        )

    def candidates(self, page: PageRecord) -> list[NoteRecord]:
        ids: set[str] = set()
        title = f.normalized_title(page.page_title)
        if not f.is_placeholder_title(page.page_title):
            ids.update(self.by_title.get(title, ()))
        ids.update(self.by_section.get(page.section_title.casefold(), ()))
        if page.normalized_text_sha256:
            ids.update(self.by_text_hash.get(page.normalized_text_sha256, ()))
        for digest in page.resource_hashes:
            ids.update(self.by_resource.get(digest, ()))
        for url in page.link_targets:
            if not url.startswith(":/"):
                ids.update(self.by_url.get(url, ()))
        for line in _blocking_lines(page.normalized_text):
            ids.update(self.by_line.get(line, ()))

        # A renamed/moved page may have no exact block. Title tokens and the
        # notebook path are cheap fallback evidence, not automatic matches.
        if not ids:
            for token in _title_tokens(title):
                ids.update(self.by_title_token.get(token, ()))
            ids.update(self.by_path_part.get(page.notebook_title.casefold(), ()))

        if len(ids) > _MAX_CANDIDATES_PER_PAGE:
            ids = set(
                sorted(
                    ids,
                    key=lambda note_id: (
                        -_blocking_rank(page, self.notes_by_id[note_id]),
                        note_id,
                    ),
                )[:_MAX_CANDIDATES_PER_PAGE]
            )
        return [self.notes_by_id[note_id] for note_id in sorted(ids)]


def _blocking_lines(text: str) -> set[str]:
    return {line for line in text.splitlines() if len(line) >= _BLOCK_LINE_MIN_LEN}


def _title_tokens(title: str) -> set[str]:
    return set(_TITLE_TOKEN_RE.findall(title))


def _blocking_rank(page: PageRecord, note: NoteRecord) -> float:
    rank = 0.0
    page_title = f.normalized_title(page.page_title)
    note_title = f.normalized_title(note.title)
    if page_title == note_title and not f.is_placeholder_title(page.page_title):
        rank += 10.0
    if note.notebook_path and page.section_title.casefold() == note.notebook_path[-1].casefold():
        rank += 5.0
    if page.normalized_text_sha256 == note.normalized_text_sha256:
        rank += 10.0
    if set(page.resource_hashes) & set(note.resource_hashes):
        rank += 8.0
    if _blocking_lines(page.normalized_text) & _blocking_lines(note.normalized_text):
        rank += 8.0
    if page.notebook_title.casefold() in {part.casefold() for part in note.notebook_path}:
        rank += 2.0
    page_tokens = _title_tokens(page_title)
    note_tokens = _title_tokens(note_title)
    if page_tokens and note_tokens:
        rank += 3.0 * len(page_tokens & note_tokens) / len(page_tokens | note_tokens)
    return rank


def _unmatched_page(page: PageRecord, config: MatchingConfig) -> MatchResult:
    return MatchResult(
        source_page_id=page.source_page_id,
        joplin_note_id=None,
        confidence=MatchConfidence.UNMATCHED,
        stage="unmatched:source",
        threshold_version=config.version,
        explanation=["no note scored above the minimum threshold"],
        source_title=page.page_title,
        source_path=list(f.source_path(page)),
    )


def _explain(feats: dict[str, float | None], score: float, margin: float) -> list[str]:
    parts = [
        f"{name}={value:.2f}" for name, value in sorted(feats.items()) if value is not None
    ]
    return [
        f"weighted score {score:.3f}, runner-up margin {margin:.3f}",
        "features: " + ", ".join(parts),
    ]


def _result(
    page: PageRecord,
    note: NoteRecord,
    confidence: MatchConfidence,
    stage: str,
    config: MatchingConfig,
    explanation: list[str],
    *,
    score: float | None = None,
    margin: float | None = None,
    features: dict[str, float | None] | None = None,
) -> MatchResult:
    return MatchResult(
        source_page_id=page.source_page_id,
        joplin_note_id=note.joplin_note_id,
        confidence=confidence,
        stage=stage,
        score=score,
        runner_up_margin=margin,
        features=features or {},
        weights=config.weights if features else {},
        threshold_version=config.version,
        explanation=explanation,
        source_title=page.page_title,
        source_path=list(f.source_path(page)),
        target_title=note.title,
        target_path=list(f.target_path(note)),
    )
