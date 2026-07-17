#!/usr/bin/env python3
"""Verify version metadata and built release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from joplin_importer import __version__  # noqa: E402

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
PRIVATE_PARTS = {
    ".env",
    "artifacts",
    "token",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def check_version_source() -> None:
    version_file = (REPO / ".version").read_text(encoding="utf-8").strip()
    if VERSION_PATTERN.fullmatch(version_file) is None:
        fail(f".version does not contain a plain semver string: {version_file!r}")
    if version_file != __version__:
        fail(f".version {version_file} != resolved package version {__version__}")

    metadata = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata.get("project", {})
    dynamic = project.get("dynamic", [])
    version_config = metadata.get("tool", {}).get("setuptools", {}).get("dynamic", {}).get(
        "version"
    )
    if "version" not in dynamic or version_config != {"file": ".version"}:
        fail("pyproject.toml no longer reads the version dynamically from .version")
    if "version" in project:
        fail("pyproject.toml contains a second, literal project.version")
    print(f"ok .version {version_file} is the single version source")


def check_tag(tag: str) -> None:
    expected = f"v{__version__}"
    if tag != expected:
        fail(f"git tag {tag!r} does not match expected tag {expected!r}")
    print(f"ok tag {tag} matches version")


def artifact_members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            return archive.getnames()
    return []


def check_private_members(path: Path) -> None:
    for member in artifact_members(path):
        pure = PurePosixPath(member)
        parts = {part.casefold() for part in pure.parts}
        if parts & PRIVATE_PARTS or pure.suffix.casefold() == ".jex":
            fail(f"private path {member!r} found in {path.name}")
        if "onenote_joplin_recovery" in member:
            fail(f"old Python package namespace found in {path.name}: {member}")


def check_wheel_metadata(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        metadata_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/METADATA")
        ]
        entry_point_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(metadata_names) != 1 or len(entry_point_names) != 1:
            fail(f"wheel {path.name} has an invalid metadata inventory")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        entry_points = archive.read(entry_point_names[0]).decode("utf-8")
    expectations = (
        "Name: joplin-importer\n",
        f"Version: {__version__}\n",
        "Requires-Python: >=3.14\n",
    )
    for expected in expectations:
        if expected not in metadata:
            fail(f"wheel metadata is missing {expected.strip()!r}")
    if "joplin-importer = joplin_importer.cli.main:main" not in entry_points:
        fail("wheel does not expose the joplin-importer console entry point")


def check_sdist_version(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        version_members = [
            member for member in archive.getmembers() if member.name.endswith("/.version")
        ]
        if len(version_members) != 1:
            fail(f"sdist {path.name} does not contain exactly one .version file")
        handle = archive.extractfile(version_members[0])
        if handle is None:
            fail(f"cannot read .version from {path.name}")
        archived_version = handle.read().decode("utf-8").strip()
    if archived_version != __version__:
        fail(f"sdist .version {archived_version!r} != {__version__!r}")


def check_local_token(path: Path) -> None:
    token_path = REPO / "token"
    if not token_path.is_file():
        return
    secret = token_path.read_bytes().strip()
    if secret and secret in path.read_bytes():
        fail(f"local token content found in {path.name}")


def check_checksums(artifacts: list[Path], checksum_path: Path) -> None:
    if not checksum_path.is_file():
        fail("dist/SHA256SUMS.txt is missing")
    expected: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            fail(f"malformed checksum line: {line!r}")
        expected[name] = digest
    actual_names = {path.name for path in artifacts}
    if set(expected) != actual_names:
        fail(f"checksum inventory {sorted(expected)} != artifacts {sorted(actual_names)}")
    for path in artifacts:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected[path.name] != actual:
            fail(f"checksum mismatch for {path.name}")


def check_artifacts() -> None:
    dist = REPO / "dist"
    if not dist.is_dir():
        print("skip artifact checks (no dist/)")
        return

    artifacts = sorted(
        path
        for path in dist.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    wheel = [path for path in artifacts if path.suffix == ".whl"]
    sdist = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheel) != 1 or len(sdist) != 1:
        fail("dist/ must contain exactly one wheel and one sdist")
    expected_stem = f"joplin_importer-{__version__}"
    if not wheel[0].name.startswith(expected_stem + "-"):
        fail(f"wheel {wheel[0].name} does not carry version {__version__}")
    if sdist[0].name != f"joplin_importer-{__version__}.tar.gz":
        fail(f"sdist {sdist[0].name} does not carry version {__version__}")
    check_wheel_metadata(wheel[0])
    check_sdist_version(sdist[0])

    for path in artifacts:
        check_private_members(path)
        check_local_token(path)
        print(f"ok artifact {path.name}")
    check_checksums(artifacts, dist / "SHA256SUMS.txt")
    print("ok SHA256SUMS.txt")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="git tag to verify against the package version")
    args = parser.parse_args()
    check_version_source()
    if args.tag:
        check_tag(args.tag)
    check_artifacts()
    print("release verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
