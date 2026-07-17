"""Read-only adapter for local OneNote section backup files."""

from .discovery import (
    BackupDiscoveryError,
    BackupInventory,
    BackupSection,
    discover_backup_root,
    discover_latest_sections,
)
from .scanner import scan_onenote_backup

__all__ = [
    "BackupDiscoveryError",
    "BackupInventory",
    "BackupSection",
    "discover_backup_root",
    "discover_latest_sections",
    "scan_onenote_backup",
]
