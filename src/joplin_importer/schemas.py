"""JSON Schema export for all persisted document formats.

Usage: ``python -m joplin_importer.schemas [output-dir]``

The generated files live in ``schemas/`` and are kept current by a unit test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from .adapters.onenote_com.quarantine import OneNoteQuarantine
from .exporting.models import (
    ExportApplyReceipt,
    ExportApproval,
    ExportDryRunReceipt,
    ExportPlan,
    ExportValidationReport,
)
from .matching.results import AuditResult, Finding, MatchResult
from .models.records import ErrorRecord, Manifest, NoteRecord, PageRecord, ScanMetadata
from .repair.models import (
    ApplyReceipt,
    ApprovalFile,
    DryRunReceipt,
    JournalEntry,
    RepairPlan,
)

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "page-record": PageRecord,
    "note-record": NoteRecord,
    "manifest": Manifest,
    "scan-metadata": ScanMetadata,
    "error-record": ErrorRecord,
    "match-result": MatchResult,
    "finding": Finding,
    "audit-result": AuditResult,
    "repair-plan": RepairPlan,
    "approval-file": ApprovalFile,
    "dry-run-receipt": DryRunReceipt,
    "apply-receipt": ApplyReceipt,
    "journal-entry": JournalEntry,
    "onenote-quarantine": OneNoteQuarantine,
    "export-plan": ExportPlan,
    "export-approval": ExportApproval,
    "export-dry-run-receipt": ExportDryRunReceipt,
    "export-apply-receipt": ExportApplyReceipt,
    "export-validation-report": ExportValidationReport,
}


def generate_schemas() -> dict[str, str]:
    """name -> pretty-printed JSON schema text."""
    result: dict[str, str] = {}
    for name, model in EXPORTED_MODELS.items():
        schema = model.model_json_schema()
        result[name] = json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return result


def write_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in generate_schemas().items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":  # pragma: no cover
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("schemas")
    for path in write_schemas(target):
        print(path)  # noqa: T201
