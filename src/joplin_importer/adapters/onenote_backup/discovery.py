"""Locate OneNote backups without depending on a localized folder name."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_VERSION_RE = re.compile(r"\d+(?:\.\d+)*\Z")
_DATED_SUFFIX_RE = re.compile(
    r"\s*\([^()]*(?:\d{1,4}[./-]\d{1,2}[./-]\d{1,4})[^()]*\)\s*\Z"
)
_RECYCLE_DIRECTORY = "onenote_recyclebin"


class BackupDiscoveryError(RuntimeError):
    """The backup root cannot be found or does not contain section files."""


@dataclass(frozen=True, slots=True)
class BackupSection:
    """The newest physical backup file for one logical OneNote section."""

    path: Path
    relative_path: str
    notebook_title: str
    section_group_path: tuple[str, ...]
    section_title: str
    is_recycle_bin: bool
    mtime_ns: int
    size: int


@dataclass(frozen=True, slots=True)
class BackupInventory:
    """Latest-only view of a physical OneNote backup directory."""

    sections: tuple[BackupSection, ...]
    physical_file_count: int
    logical_section_count: int
    older_versions_skipped: int
    recycle_bin_files_skipped: int


def discover_backup_root(
    override: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Return a validated backup root, preferring an explicit override.

    Automatic discovery searches numeric OneNote version directories below
    ``LOCALAPPDATA`` and scores their immediate children by recursively found
    ``.one`` files. It never relies on the localized backup directory name.
    """

    if override is not None:
        return _validate_root(override)

    environment = os.environ if env is None else env
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise BackupDiscoveryError(
            "cannot auto-discover OneNote backups because LOCALAPPDATA is not set; "
            "pass --backup-root"
        )

    base = Path(local_app_data) / "Microsoft" / "OneNote"
    if not base.is_dir():
        raise BackupDiscoveryError(
            "cannot auto-discover OneNote backups below LOCALAPPDATA; pass --backup-root"
        )

    candidates: list[tuple[int, int, tuple[int, ...], int, str, Path]] = []
    for version_dir in base.iterdir():
        if not version_dir.is_dir() or not _VERSION_RE.fullmatch(version_dir.name):
            continue
        version_key = tuple(int(part) for part in version_dir.name.split("."))
        for child in version_dir.iterdir():
            if not child.is_dir():
                continue
            files = _one_files(child)
            if not files:
                continue
            dated_count = sum(1 for path in files if _DATED_SUFFIX_RE.search(path.stem))
            newest = max(path.stat().st_mtime_ns for path in files)
            candidates.append(
                (dated_count, len(files), version_key, newest, child.name.casefold(), child)
            )

    if not candidates:
        raise BackupDiscoveryError(
            "cannot auto-discover a OneNote backup directory containing .one files; "
            "pass --backup-root"
        )
    return max(candidates)[-1].resolve()


def discover_latest_sections(
    root: Path,
    *,
    include_recycle_bin: bool = False,
    notebook_filter: str | None = None,
) -> BackupInventory:
    """Select only the newest file for every logical section."""

    root = _validate_root(root)
    all_files = _one_files(root)
    selected_scope: list[BackupSection] = []
    recycle_skipped = 0

    for path in all_files:
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            continue
        notebook_title = relative.parts[0]
        if notebook_filter and notebook_title != notebook_filter:
            continue
        groups = tuple(relative.parts[1:-1])
        in_recycle_bin = any(part.casefold() == _RECYCLE_DIRECTORY for part in groups)
        if in_recycle_bin and not include_recycle_bin:
            recycle_skipped += 1
            continue
        stat = path.stat()
        selected_scope.append(
            BackupSection(
                path=path,
                relative_path=relative.as_posix(),
                notebook_title=notebook_title,
                section_group_path=groups,
                section_title=_logical_section_title(path.stem),
                is_recycle_bin=in_recycle_bin,
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
            )
        )

    newest: dict[tuple[str, ...], BackupSection] = {}
    for section in selected_scope:
        key = tuple(
            part.casefold()
            for part in (
                section.notebook_title,
                *section.section_group_path,
                section.section_title,
            )
        )
        previous = newest.get(key)
        if previous is None or _freshness_key(section) > _freshness_key(previous):
            newest[key] = section

    sections = tuple(
        sorted(
            newest.values(),
            key=lambda item: (
                item.notebook_title.casefold(),
                tuple(part.casefold() for part in item.section_group_path),
                item.section_title.casefold(),
                item.relative_path.casefold(),
            ),
        )
    )
    return BackupInventory(
        sections=sections,
        physical_file_count=len(selected_scope),
        logical_section_count=len(sections),
        older_versions_skipped=len(selected_scope) - len(sections),
        recycle_bin_files_skipped=recycle_skipped,
    )


def _logical_section_title(stem: str) -> str:
    stripped = _DATED_SUFFIX_RE.sub("", stem).rstrip()
    return stripped or stem


def _freshness_key(section: BackupSection) -> tuple[int, str, str]:
    return (section.mtime_ns, section.relative_path.casefold(), section.relative_path)


def _one_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.casefold() == ".one"),
        key=lambda path: path.as_posix().casefold(),
    )


def _validate_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if not resolved.is_dir():
        raise BackupDiscoveryError("OneNote backup root is not an existing directory")
    if not _one_files(resolved):
        raise BackupDiscoveryError("OneNote backup root contains no .one files")
    return resolved
