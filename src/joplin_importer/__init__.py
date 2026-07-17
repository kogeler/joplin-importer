"""Joplin Importer: OneNote snapshot analysis and complete export."""

from __future__ import annotations


def _resolve_version() -> str:
    """Resolve the version from the checkout or installed distribution."""
    try:
        from importlib import resources

        return resources.files(__name__).joinpath(".version").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    try:
        from pathlib import Path

        return (Path(__file__).resolve().parents[2] / ".version").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        pass

    try:
        from importlib import metadata

        return metadata.version("joplin-importer")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()
