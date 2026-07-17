"""Detection rule tests."""

from joplin_importer.matching.detection import (
    detect_findings,
    estimate_import_window,
)
from joplin_importer.matching.engine import match_records
from joplin_importer.models import (
    CauseClass,
    EvidenceClass,
    FindingKind,
    ResourceRecord,
    ResourceStatus,
)
from tests.unit.test_matching import make_note, make_page

NO_MODELS = {"load_page_model": lambda _id: None, "load_note_model": lambda _id: None}


def run_detect(pages, notes, **kwargs):
    matches = match_records(pages, notes)
    params = {**NO_MODELS, **kwargs}
    return detect_findings(pages, notes, matches, **params)


def kinds(findings):
    return {f.kind for f in findings}


def find(findings, kind):
    return next(f for f in findings if f.kind == kind)


# -- import window -----------------------------------------------------------


def test_import_window_from_clustered_created_times():
    notes = [
        make_note(f"n{i}", f"N{i}", "x", created=f"2025-06-01T10:00:{i:02d}Z")
        for i in range(10)
    ]
    for n in notes:
        n.created_at = n.user_created_at  # created_time is the import run time
    window = estimate_import_window(notes)
    assert window.start_s is not None
    assert window.end_s is not None


def test_import_window_unknown_when_spread():
    notes = []
    for i in range(10):
        note = make_note(f"n{i}", f"N{i}", "x")
        note.created_at = f"20{10 + i}-01-01T00:00:00Z"
        notes.append(note)
    window = estimate_import_window(notes)
    assert window.start_s is None


# -- core rules -----------------------------------------------------------------


def test_missing_source_page():
    pages = [make_page("{p1}", "Lost page", "content that vanished")]
    notes = [make_note("n1", "Other", "unrelated", path=["Personal"])]
    findings = run_detect(pages, notes)
    finding = find(findings, FindingKind.SOURCE_PAGE_MISSING)
    assert finding.cause == CauseClass.MIGRATION_LOSS
    assert finding.source_page_id == "{p1}"


def test_empty_note_with_source_text():
    pages = [make_page("{p1}", "Report", "long meaningful content here")]
    notes = [make_note("n1", "Report", "")]
    findings = run_detect(pages, notes)
    finding = find(findings, FindingKind.EMPTY_BODY)
    assert finding.evidence == EvidenceClass.CONFIRMED
    assert finding.joplin_note_id == "n1"


def test_image_only_page_is_not_empty():
    page = make_page("{p1}", "Photos", "")
    page.image_count = 2
    page.resource_hashes = ["img-1", "img-2"]
    page.resources = [
        ResourceRecord(source_reference="image:1", sha256="img-1", is_image=True),
        ResourceRecord(source_reference="image:2", sha256="img-2", is_image=True),
    ]
    note = make_note("n1", "Photos", "")
    findings = run_detect([page], [note])
    finding = find(findings, FindingKind.EMPTY_BODY)
    assert "only images/attachments" in finding.explanation


def test_truncated_text():
    body = "word " * 200
    pages = [make_page("{p1}", "Long", body)]
    notes = [make_note("n1", "Long", "word " * 20)]
    findings = run_detect(pages, notes)
    finding = find(findings, FindingKind.TRUNCATED_TEXT)
    assert finding.evidence == EvidenceClass.PROBABLE


def test_missing_images_by_hash():
    page = make_page("{p1}", "Pics", "text body")
    page.image_count = 2
    page.resources = [
        ResourceRecord(source_reference="image:1", sha256="h1", is_image=True),
        ResourceRecord(source_reference="image:2", sha256="h2", is_image=True),
    ]
    page.resource_hashes = ["h1", "h2"]
    note = make_note("n1", "Pics", "text body")
    note.image_count = 1
    note.resources = [
        ResourceRecord(source_reference=":/" + "a" * 32, sha256="h1", is_image=True)
    ]
    note.resource_hashes = ["h1"]
    findings = run_detect([page], [note])
    finding = find(findings, FindingKind.MISSING_IMAGES)
    assert finding.evidence == EvidenceClass.CONFIRMED


def test_reencoded_images_are_uncertain_not_missing():
    page = make_page("{p1}", "Pics", "text body")
    page.image_count = 1
    page.resources = [
        ResourceRecord(source_reference="image:1", sha256="orig", is_image=True)
    ]
    page.resource_hashes = ["orig"]
    note = make_note("n1", "Pics", "text body")
    note.image_count = 1
    note.resources = [
        ResourceRecord(source_reference=":/" + "a" * 32, sha256="reencoded", is_image=True)
    ]
    note.resource_hashes = ["reencoded"]
    findings = run_detect([page], [note])
    assert FindingKind.MISSING_IMAGES not in kinds(findings)
    finding = find(findings, FindingKind.RESOURCE_HASH_MISMATCH)
    assert finding.evidence == EvidenceClass.UNCERTAIN


def test_broken_resource_reference():
    page = make_page("{p1}", "Note", "body text")
    note = make_note("n1", "Note", "body text")
    note.resources = [
        ResourceRecord(
            source_reference=":/" + "b" * 32,
            status=ResourceStatus.MISSING,
            warnings=["referenced in body but not attached"],
        )
    ]
    findings = run_detect([page], [note])
    assert FindingKind.BROKEN_RESOURCE_REFERENCE in kinds(findings)


def test_placeholder_title():
    pages = [make_page("{p1}", "Real title", "identical body content here")]
    notes = [make_note("n1", "Untitled Page", "identical body content here")]
    findings = run_detect(pages, notes)
    finding = find(findings, FindingKind.PLACEHOLDER_TITLE)
    assert finding.evidence == EvidenceClass.CONFIRMED


def test_lost_timestamps():
    pages = [make_page("{p1}", "Note", "body", created="2020-01-01T00:00:00Z")]
    notes = [make_note("n1", "Note", "body", created="2025-06-01T00:00:00Z")]
    findings = run_detect(pages, notes)
    assert FindingKind.LOST_TIMESTAMPS in kinds(findings)


def test_wrong_placement():
    pages = [make_page("{p1}", "Note", "body content", section="Tasks")]
    notes = [make_note("n1", "Note", "body content", path=["Work", "Wrong section"])]
    findings = run_detect(pages, notes)
    assert FindingKind.WRONG_PLACEMENT in kinds(findings)


def test_lost_hierarchy_for_subpage():
    pages = [make_page("{p1}", "Sub", "body content", level=2)]
    notes = [make_note("n1", "Sub", "body content")]
    findings = run_detect(pages, notes)
    finding = find(findings, FindingKind.LOST_HIERARCHY)
    assert finding.cause == CauseClass.FORMAT_CONVERSION


def test_unsupported_content_reported():
    page = make_page("{p1}", "Ink page", "body content")
    page.warnings = ["unsupported OneNote object: InkDrawing x2"]
    notes = [make_note("n1", "Ink page", "body content")]
    findings = run_detect([page], notes)
    finding = find(findings, FindingKind.UNSUPPORTED_CONTENT)
    assert "InkDrawing" in finding.explanation


def test_duplicate_targets_by_embedded_id():
    pages = [make_page("{p1}", "Orig", "content")]
    notes = [
        make_note("n1", "Orig", "content", embedded_id="{p1}"),
        make_note("n2", "Orig copy", "content", embedded_id="{p1}"),
    ]
    findings = run_detect(pages, notes)
    assert FindingKind.DUPLICATE_TARGETS in kinds(findings)


def test_collapsed_sources():
    part_a = "unique first part of the meeting notes with agenda and attendees list"
    part_b = "second half discussing the budget details and the quarterly hiring plan"
    page_a = make_page("{p1}", "Part A", part_a)
    page_b = make_page("{p2}", "Part B", part_b)
    merged = make_note("n1", "Part A", f"{part_a}\n\n{part_b}")
    findings = run_detect([page_a, page_b], [merged])
    collapsed = [f for f in findings if f.kind == FindingKind.COLLAPSED_SOURCES]
    assert collapsed
    assert collapsed[0].source_page_id == "{p2}"


def test_source_drift_softens_cause():
    # import window ~2025-06; page edited later in 2026
    notes = [
        make_note(f"n{i}", f"Note {i}", "stable content " + str(i))
        for i in range(6)
    ]
    for n in notes:
        n.created_at = "2025-06-01T10:00:00Z"
    page = make_page(
        "{p1}",
        "Note 0",
        "stable content 0 plus a large amount of newly added text " + "x" * 200,
        updated="2026-05-01T00:00:00Z",
    )
    pages = [page]
    findings = run_detect(pages, notes)
    drift = [f for f in findings if f.kind == FindingKind.SOURCE_DRIFT]
    assert drift
    truncation = [f for f in findings if f.kind == FindingKind.TRUNCATED_TEXT]
    for f in truncation:
        assert f.cause == CauseClass.SOURCE_DRIFT
        assert f.evidence == EvidenceClass.UNCERTAIN


def test_target_extra_note():
    pages = [make_page("{p1}", "Match", "shared body")]
    notes = [
        make_note("n1", "Match", "shared body"),
        make_note("n2", "Own note", "user wrote this later", path=["Personal"]),
    ]
    findings = run_detect(pages, notes)
    extra = find(findings, FindingKind.TARGET_NOTE_UNMATCHED)
    assert extra.joplin_note_id == "n2"
    assert extra.cause == CauseClass.TARGET_EXTRA
