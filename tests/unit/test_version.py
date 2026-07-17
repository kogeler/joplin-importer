import re
import tomllib
from pathlib import Path

import joplin_importer

REPO = Path(__file__).resolve().parents[2]


def test_version_file_is_single_source():
    version_file = (REPO / ".version").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version_file)
    assert joplin_importer.__version__ == version_file


def test_pyproject_reads_version_dynamically():
    metadata = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["dynamic"] == ["version"]
    assert "version" not in metadata["project"]
    assert metadata["tool"]["setuptools"]["dynamic"]["version"] == {
        "file": ".version"
    }


def test_distribution_and_cli_use_new_name():
    metadata = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["name"] == "joplin-importer"
    assert metadata["project"]["scripts"] == {
        "joplin-importer": "joplin_importer.cli.main:main"
    }
