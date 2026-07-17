"""Labeled synthetic fixtures for the matching engine."""

from joplin_importer.matching.engine import _CandidateIndex, match_records
from joplin_importer.models import (
    AuditRole,
    ContentFormat,
    MatchConfidence,
    NoteRecord,
    PageRecord,
    SourceBackend,
)
from joplin_importer.normalization import Normalizer

norm = Normalizer()


def make_page(
    page_id: str,
    title: str,
    body_md: str = "",
    *,
    section: str = "Tasks",
    notebook: str = "Work",
    groups: list[str] | None = None,
    created: str | None = "2025-01-01T08:00:00Z",
    updated: str | None = "2025-01-02T08:00:00Z",
    level: int = 1,
    resource_hashes: list[str] | None = None,
) -> PageRecord:
    normalized = norm.normalize(ContentFormat.MARKDOWN, body_md)
    return PageRecord(
        source_backend=SourceBackend.ONENOTE_COM,
        audit_role=AuditRole.AUTHORITATIVE_CURRENT,
        source_page_id=page_id,
        notebook_title=notebook,
        section_group_path=groups or [],
        section_title=section,
        page_title=title,
        page_level=level,
        created_at=created,
        updated_at=updated,
        normalizer_version=normalized.version,
        normalized_text=normalized.normalized_text,
        normalized_text_sha256=normalized.normalized_text_sha256,
        semantic_model_sha256=normalized.semantic_sha256,
        visible_text_length=len(normalized.normalized_text),
        link_targets=[],
        resource_hashes=resource_hashes or [],
    )


def make_note(
    note_id: str,
    title: str,
    body_md: str = "",
    *,
    path: list[str] | None = None,
    created: str | None = "2025-01-01T08:00:30Z",
    updated: str | None = "2025-01-02T08:00:30Z",
    embedded_id: str | None = None,
    resource_hashes: list[str] | None = None,
) -> NoteRecord:
    normalized = norm.normalize(ContentFormat.MARKDOWN, body_md)
    return NoteRecord(
        joplin_note_id=note_id,
        title=title,
        notebook_path=path if path is not None else ["Work", "Tasks"],
        user_created_at=created,
        user_updated_at=updated,
        embedded_onenote_page_id=embedded_id,
        normalizer_version=normalized.version,
        normalized_text=normalized.normalized_text,
        normalized_text_sha256=normalized.normalized_text_sha256,
        semantic_model_sha256=normalized.semantic_sha256,
        visible_text_length=len(normalized.normalized_text),
        link_targets=[],
        resource_hashes=resource_hashes or [],
    )


def by_page(results):
    return {r.source_page_id: r for r in results if r.source_page_id}


def test_embedded_source_id_wins():
    pages = [make_page("{p1}", "Anything", "content A")]
    notes = [make_note("n1", "Renamed completely", "different body", embedded_id="{p1}")]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.confidence == MatchConfidence.EXACT
    assert result.stage == "deterministic:embedded-source-id"
    assert result.joplin_note_id == "n1"


def test_path_title_time_deterministic():
    pages = [make_page("{p1}", "Weekly report", "body text here")]
    notes = [make_note("n1", "Weekly report", "totally different body")]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.confidence == MatchConfidence.EXACT
    assert result.stage == "deterministic:path-title-time"


def test_semantic_hash_deterministic_across_titles():
    body = "# Heading\n\nUnique paragraph about quarterly synergy.\n\n- item"
    pages = [make_page("{p1}", "Original title", body, created=None)]
    notes = [make_note("n1", "Untitled Page", body, path=["Other"], created=None)]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.confidence == MatchConfidence.EXACT
    assert result.stage == "deterministic:semantic-hash"


def test_duplicate_untitled_empty_pages_not_paired_by_title():
    pages = [
        make_page("{p1}", "Untitled Page", "", created="2025-01-01T08:00:00Z"),
        make_page("{p2}", "Untitled Page", "", created="2025-03-03T08:00:00Z"),
    ]
    notes = [
        make_note("n1", "Untitled Page", "", created="2025-06-01T00:00:00Z"),
        make_note("n2", "Untitled Page", "", created="2025-06-01T00:00:01Z"),
    ]
    results = by_page(match_records(pages, notes))
    for page_id in ["{p1}", "{p2}"]:
        assert results[page_id].confidence in (
            MatchConfidence.UNMATCHED,
            MatchConfidence.AMBIGUOUS,
        ), results[page_id]
        assert results[page_id].stage != "deterministic:path-title-time"


def test_duplicate_titles_disambiguated_by_content():
    pages = [
        make_page("{p1}", "Meeting notes", "Discussed alpha launch and hiring."),
        make_page("{p2}", "Meeting notes", "Budget review, headcount freeze."),
    ]
    notes = [
        make_note("n1", "Meeting notes", "Discussed alpha launch and hiring."),
        make_note("n2", "Meeting notes", "Budget review, headcount freeze."),
    ]
    results = by_page(match_records(pages, notes))
    assert results["{p1}"].joplin_note_id == "n1"
    assert results["{p2}"].joplin_note_id == "n2"


def test_truncated_note_still_matches_scored():
    body = "\n\n".join(f"Paragraph {i} with distinctive content phrase." for i in range(8))
    truncated = "Paragraph 0 with distinctive content phrase."
    pages = [make_page("{p1}", "Long page", body)]
    notes = [make_note("n1", "Long page", truncated, created="2025-01-01T08:05:00Z")]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.joplin_note_id == "n1"
    assert result.confidence in (MatchConfidence.HIGH_CONFIDENCE, MatchConfidence.PROBABLE)
    assert result.score is not None
    assert result.features  # full explanation stored
    assert result.weights


def test_empty_note_matches_by_title_path_time():
    # match identity is separate from content integrity: the empty body must
    # not prevent identification via path+title+time (rule A2)
    pages = [make_page("{p1}", "Project kickoff", "Real content that was lost")]
    notes = [make_note("n1", "Project kickoff", "", created="2025-01-01T08:00:10Z")]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.joplin_note_id == "n1"
    assert result.confidence in (
        MatchConfidence.EXACT,
        MatchConfidence.HIGH_CONFIDENCE,
        MatchConfidence.PROBABLE,
    )


def test_unmatched_source_page():
    pages = [make_page("{p1}", "Vanished page", "content never imported")]
    notes = [make_note("n1", "Unrelated", "something else entirely", path=["Personal"])]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.confidence == MatchConfidence.UNMATCHED
    assert result.joplin_note_id is None


def test_unmatched_target_note_reported():
    pages = [make_page("{p1}", "Source", "content")]
    notes = [
        make_note("n1", "Source", "content"),
        make_note("n2", "User's own note", "written directly in joplin", path=["Personal"]),
    ]
    results = match_records(pages, notes)
    unmatched_targets = [r for r in results if r.stage == "unmatched:target"]
    assert [r.joplin_note_id for r in unmatched_targets] == ["n2"]


def test_resource_hashes_help_matching():
    pages = [
        make_page("{p1}", "Untitled Page", "", resource_hashes=["hash-a"], created=None),
        make_page("{p2}", "Untitled Page", "", resource_hashes=["hash-b"], created=None),
    ]
    notes = [
        make_note("n1", "Untitled Page", "", resource_hashes=["hash-b"], created=None),
        make_note("n2", "Untitled Page", "", resource_hashes=["hash-a"], created=None),
    ]
    results = by_page(match_records(pages, notes))
    if results["{p1}"].joplin_note_id:
        assert results["{p1}"].joplin_note_id == "n2"
    if results["{p2}"].joplin_note_id:
        assert results["{p2}"].joplin_note_id == "n1"


def test_unicode_titles():
    pages = [make_page("{p1}", "Заметки проекта", "содержимое страницы")]
    notes = [make_note("n1", "Заметки проекта", "содержимое страницы")]
    result = by_page(match_records(pages, notes))["{p1}"]
    assert result.confidence == MatchConfidence.EXACT


def test_same_source_never_matched_twice():
    body = "Shared duplicated body content for both notes."
    pages = [make_page("{p1}", "Dup", body)]
    notes = [
        make_note("n1", "Dup", body, created="2025-01-01T08:00:00Z"),
        make_note("n2", "Dup", body, created="2025-01-01T08:00:00Z"),
    ]
    results = match_records(pages, notes)
    matched = [r for r in results if r.source_page_id == "{p1}" and r.joplin_note_id]
    assert len(matched) <= 1


def test_unique_path_title_matches_when_forensic_source_has_no_timestamp():
    pages = [make_page("{p1}", "Backup page", "source body", created=None)]
    notes = [make_note("n1", "Backup page", "converted body")]

    result = by_page(match_records(pages, notes))["{p1}"]

    assert result.joplin_note_id == "n1"
    assert result.confidence == MatchConfidence.HIGH_CONFIDENCE
    assert result.stage == "deterministic:unique-path-title-without-source-time"


def test_unique_title_matches_moved_note_without_claiming_exact_confidence():
    pages = [make_page("{p1}", "Distinct forensic title", "source", created=None)]
    notes = [
        make_note(
            "n1",
            "Distinct forensic title",
            "converted",
            path=["Different", "Placement"],
        )
    ]

    result = by_page(match_records(pages, notes))["{p1}"]

    assert result.joplin_note_id == "n1"
    assert result.confidence == MatchConfidence.PROBABLE
    assert result.stage == "deterministic:unique-title-without-source-time"


def test_candidate_blocking_keeps_shared_content_and_bounds_distractors():
    shared = "A uniquely shared long fragment used to identify the converted page."
    page = make_page(
        "{p1}",
        "Original name",
        shared + "\n\nsource remainder",
        section="Source section",
        notebook="Source notebook",
        created=None,
    )
    wanted = make_note(
        "wanted",
        "Renamed",
        shared + "\n\ntarget remainder",
        path=["Different", "Path"],
        created=None,
    )
    distractors = [
        make_note(
            f"n{index}",
            f"Unrelated {index}",
            f"Unrelated body {index}",
            path=["Source notebook", "Other"],
            created=None,
        )
        for index in range(150)
    ]

    candidates = _CandidateIndex.build([wanted, *distractors]).candidates(page)

    assert wanted in candidates
    assert len(candidates) <= 100
