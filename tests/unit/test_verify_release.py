import zipfile

import pytest

from joplin_importer import __version__
from scripts.build_standalone import standalone_name
from scripts.verify_release import STANDALONE_NAMES, check_standalones, check_wheel_metadata


def test_wheel_metadata_accepts_windows_line_endings(tmp_path):
    wheel = tmp_path / "joplin_importer-test-py3-none-any.whl"
    dist_info = "joplin_importer-test.dist-info"
    metadata = (
        "Metadata-Version: 2.4\r\n"
        "Name: joplin-importer\r\n"
        f"Version: {__version__}\r\n"
        "Requires-Python: >=3.14\r\n"
        "\r\n"
    )
    entry_points = (
        "[console_scripts]\r\n"
        "joplin-importer = joplin_importer.cli.main:main\r\n"
    )

    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{dist_info}/METADATA", metadata)
        archive.writestr(f"{dist_info}/entry_points.txt", entry_points)

    check_wheel_metadata(wheel)


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", "joplin-importer-linux-amd64"),
        ("linux", "aarch64", "joplin-importer-linux-arm64"),
        ("win32", "AMD64", "joplin-importer-windows-amd64.exe"),
    ],
)
def test_standalone_name(system, machine, expected):
    assert standalone_name(system, machine) == expected


def test_standalone_name_rejects_windows_arm64():
    with pytest.raises(ValueError, match="unsupported standalone target"):
        standalone_name("win32", "ARM64")


def test_release_requires_all_standalones_when_requested(tmp_path):
    standalones = [tmp_path / name for name in sorted(STANDALONE_NAMES)]
    for standalone in standalones:
        standalone.touch()

    check_standalones(standalones, require_all=True)

    with pytest.raises(SystemExit):
        check_standalones(standalones[:-1], require_all=True)
