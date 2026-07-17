"""Validated page quarantine for unstable OneNote COM content reads.

Quarantine entries are matched against the hierarchy before any
``GetPageContent`` call.  The exact OneNote page ID is authoritative; the
expected title is retained as a human-readable guard and diagnostic.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from ...models.hashing import sha256_canonical_json

QUARANTINE_SCHEMA_VERSION = 1


class QuarantineError(ValueError):
    """The quarantine file cannot be read or does not match its schema."""


class QuarantinedPage(BaseModel):
    """One page that must not be passed to ``GetPageContent``."""

    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1, description="Exact OneNote hierarchy page ID")
    expected_title: str = Field(
        min_length=1,
        description="Human-readable title expected in the current hierarchy",
    )
    reason: str = Field(min_length=1, description="Why this page is quarantined")

    @field_validator("page_id", "reason")
    @classmethod
    def strip_nonempty_fields(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("expected_title")
    @classmethod
    def preserve_nonempty_title(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class OneNoteQuarantine(BaseModel):
    """Versioned local input controlling intentional COM page skips."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    pages: list[QuarantinedPage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> OneNoteQuarantine:
        counts = Counter(entry.page_id for entry in self.pages)
        duplicates = sorted(page_id for page_id, count in counts.items() if count > 1)
        if duplicates:
            raise ValueError("duplicate quarantine page_id(s): " + ", ".join(duplicates))
        return self

    def by_page_id(self) -> dict[str, QuarantinedPage]:
        return {entry.page_id: entry for entry in self.pages}

    def digest(self) -> str:
        return sha256_canonical_json(self.model_dump(mode="json"))


def load_quarantine(path: Path) -> OneNoteQuarantine:
    """Read and strictly validate a quarantine JSON file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise QuarantineError(f"cannot read quarantine file {path}: {exc}") from exc
    try:
        return OneNoteQuarantine.model_validate_json(text)
    except ValidationError as exc:
        raise QuarantineError(f"invalid quarantine file {path}: {exc}") from exc
