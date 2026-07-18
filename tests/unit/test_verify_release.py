import zipfile

from joplin_importer import __version__
from scripts.verify_release import check_wheel_metadata


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
