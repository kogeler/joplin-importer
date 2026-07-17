import pytest

from scripts.check_version_increment import parse_version, project_version


def test_parse_version_orders_semver_numerically():
    assert parse_version("1.10.0", "current") > parse_version("1.9.9", "base")
    assert parse_version("2.0.0", "current") > parse_version("1.99.99", "base")


def test_parse_version_rejects_non_release_version():
    with pytest.raises(ValueError, match="plain semver"):
        parse_version("1.2.0-rc1", ".version")


def test_project_version_supports_first_version_file_migration():
    assert project_version('[project]\nversion = "0.1.0"\n', "base") == "0.1.0"
