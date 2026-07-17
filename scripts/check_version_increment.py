#!/usr/bin/env python3
"""Check that the working tree version is newer than a Git base revision."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
Version = tuple[int, int, int]


def parse_version(value: str, source: str) -> Version:
    match = VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"{source} does not contain a plain semver version: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def project_version(pyproject_text: str, source: str) -> str:
    try:
        value = tomllib.loads(pyproject_text)["project"]["version"]
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read project.version from {source}: {exc}") from exc
    if not isinstance(value, str):
        raise ValueError(f"{source} project.version is not a string")
    return value


def read_git_file(base_ref: str, path: str) -> str | None:
    # Arguments are passed directly to Git; ``path`` is always a repository
    # constant and no shell is involved.
    result = subprocess.run(  # noqa: S603
        ["git", "show", f"{base_ref}:{path}"],  # noqa: S607
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def read_base_version(base_ref: str) -> str:
    version_file = read_git_file(base_ref, ".version")
    if version_file is not None:
        return version_file.strip()

    # Migration path for the first PR that introduces .version.
    pyproject = read_git_file(base_ref, "pyproject.toml")
    if pyproject is not None:
        return project_version(pyproject, f"{base_ref}:pyproject.toml").strip()
    raise ValueError(f"cannot read a version source from base ref {base_ref!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="Git revision to compare against")
    args = parser.parse_args()

    try:
        current_text = (REPO / ".version").read_text(encoding="utf-8").strip()
        base_text = read_base_version(args.base_ref)
        current = parse_version(current_text, "current .version")
        base = parse_version(base_text, f"{args.base_ref} version")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if current <= base:
        parser.error(
            ".version must be incremented relative to the PR base: "
            f"current {current_text}, base {base_text}"
        )

    print(f"ok .version incremented: {base_text} -> {current_text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
