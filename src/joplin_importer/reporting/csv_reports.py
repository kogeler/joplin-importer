"""CSV report generation."""

from __future__ import annotations

import csv
from pathlib import Path

from ..matching.results import AuditResult
from ..models import FindingKind, MatchConfidence

_MATCH_COLUMNS = [
    "source_page_id",
    "joplin_note_id",
    "confidence",
    "stage",
    "score",
    "runner_up_margin",
    "source_title",
    "source_path",
    "target_title",
    "target_path",
    "explanation",
]

_FINDING_COLUMNS = [
    "kind",
    "evidence",
    "cause",
    "source_page_id",
    "joplin_note_id",
    "title",
    "path",
    "explanation",
]


def write_csv_reports(result: AuditResult, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    written.append(_write_matches(result, output_dir / "matches.csv"))
    written.append(
        _write_findings(
            result,
            output_dir / "missing-pages.csv",
            {FindingKind.SOURCE_PAGE_MISSING},
        )
    )
    written.append(
        _write_findings(
            result,
            output_dir / "empty-or-truncated.csv",
            {FindingKind.EMPTY_BODY, FindingKind.TRUNCATED_TEXT},
        )
    )
    written.append(
        _write_findings(
            result,
            output_dir / "missing-resources.csv",
            {
                FindingKind.MISSING_IMAGES,
                FindingKind.MISSING_ATTACHMENTS,
                FindingKind.RESOURCE_HASH_MISMATCH,
                FindingKind.BROKEN_RESOURCE_REFERENCE,
            },
        )
    )
    written.append(_write_ambiguous(result, output_dir / "ambiguous.csv"))
    written.append(
        _write_findings(
            result,
            output_dir / "format-differences.csv",
            {
                FindingKind.REPRESENTATION_ONLY,
                FindingKind.FORMAT_CONVERSION_LOSS,
                FindingKind.UNSUPPORTED_CONTENT,
                FindingKind.LOST_HIERARCHY,
            },
        )
    )
    written.append(
        _write_findings(
            result, output_dir / "source-drift.csv", {FindingKind.SOURCE_DRIFT}
        )
    )
    return written


def write_extractor_errors_csv(errors: list[dict], output_dir: Path) -> Path:
    """errors: list of error record dicts augmented with a 'snapshot' key."""
    path = output_dir / "extractor-errors.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["snapshot", "scope", "item_id", "item_title", "exception_type", "message"],
        )
        writer.writeheader()
        for error in errors:
            writer.writerow({k: error.get(k, "") for k in writer.fieldnames})
    return path


def _write_matches(result: AuditResult, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MATCH_COLUMNS)
        writer.writeheader()
        for match in result.matches:
            writer.writerow(
                {
                    "source_page_id": match.source_page_id,
                    "joplin_note_id": match.joplin_note_id or "",
                    "confidence": str(match.confidence),
                    "stage": match.stage,
                    "score": f"{match.score:.4f}" if match.score is not None else "",
                    "runner_up_margin": (
                        f"{match.runner_up_margin:.4f}"
                        if match.runner_up_margin is not None
                        else ""
                    ),
                    "source_title": match.source_title,
                    "source_path": " / ".join(match.source_path),
                    "target_title": match.target_title,
                    "target_path": " / ".join(match.target_path),
                    "explanation": " | ".join(match.explanation),
                }
            )
    return path


def _write_findings(result: AuditResult, path: Path, kinds: set[FindingKind]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FINDING_COLUMNS)
        writer.writeheader()
        for finding in result.findings:
            if finding.kind not in kinds:
                continue
            writer.writerow(
                {
                    "kind": str(finding.kind),
                    "evidence": str(finding.evidence),
                    "cause": str(finding.cause),
                    "source_page_id": finding.source_page_id or "",
                    "joplin_note_id": finding.joplin_note_id or "",
                    "title": finding.title,
                    "path": " / ".join(finding.path),
                    "explanation": finding.explanation,
                }
            )
    return path


def _write_ambiguous(result: AuditResult, path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=_MATCH_COLUMNS)
        writer.writeheader()
        for match in result.matches:
            if match.confidence != MatchConfidence.AMBIGUOUS:
                continue
            writer.writerow(
                {
                    "source_page_id": match.source_page_id,
                    "joplin_note_id": match.joplin_note_id or "",
                    "confidence": str(match.confidence),
                    "stage": match.stage,
                    "score": f"{match.score:.4f}" if match.score is not None else "",
                    "runner_up_margin": (
                        f"{match.runner_up_margin:.4f}"
                        if match.runner_up_margin is not None
                        else ""
                    ),
                    "source_title": match.source_title,
                    "source_path": " / ".join(match.source_path),
                    "target_title": match.target_title,
                    "target_path": " / ".join(match.target_path),
                    "explanation": " | ".join(match.explanation),
                }
            )
    return path
