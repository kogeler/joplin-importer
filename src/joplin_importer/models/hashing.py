"""Deterministic hashing helpers.

All cross-machine hashes in this project must be reproducible: same logical
content -> same hash, independent of dict ordering, platform, or locale.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of a byte string."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of text encoded as UTF-8."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically.

    Sorted keys, no insignificant whitespace, no ASCII escaping so that the
    output is stable and human-diffable across platforms.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_canonical_json(value: Any) -> str:
    """Hex SHA-256 of the canonical JSON serialization of *value*."""
    return sha256_text(canonical_json(value))
