"""Timestamp normalization.

All timestamps stored in snapshots are ISO 8601 UTC with second precision
(``YYYY-MM-DDTHH:MM:SSZ``). Sources report time differently (OneNote XML uses
ISO with offsets, Joplin uses Unix milliseconds); matching applies explicit
tolerances instead of demanding byte-identical strings.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_iso_from_epoch_ms(epoch_ms: int | float | None) -> str | None:
    """Joplin-style Unix milliseconds -> canonical UTC ISO string."""
    if epoch_ms is None or epoch_ms <= 0:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso_from_string(value: str | None) -> str | None:
    """Best-effort ISO 8601 (with or without offset) -> canonical UTC ISO string."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch_seconds(iso_utc: str | None) -> float | None:
    """Canonical UTC ISO string -> Unix seconds (for tolerance comparisons)."""
    if not iso_utc:
        return None
    try:
        return datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def now_utc_iso() -> str:
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
