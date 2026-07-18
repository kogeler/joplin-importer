#!/usr/bin/env python3
"""Build the current platform's one-file joplin-importer executable."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def standalone_architecture(machine: str = platform.machine()) -> str:
    architectures = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    architecture = architectures.get(machine.casefold())
    if architecture is None:
        raise ValueError(f"unsupported standalone architecture: {machine}")
    return architecture


def standalone_name(system: str = sys.platform, machine: str = platform.machine()) -> str:
    architecture = standalone_architecture(machine)
    if system == "linux":
        return f"joplin-importer-linux-{architecture}"
    if system == "win32" and architecture == "amd64":
        return f"joplin-importer-windows-{architecture}.exe"
    raise ValueError(f"unsupported standalone target: {system}/{architecture}")


def build() -> Path:
    import PyInstaller.__main__

    artifact_name = standalone_name()
    executable_name = artifact_name.removesuffix(".exe")
    dist = REPO / "dist"
    work = REPO / "build" / "pyinstaller"
    spec = work / "spec"
    dist.mkdir(parents=True, exist_ok=True)
    spec.mkdir(parents=True, exist_ok=True)

    PyInstaller.__main__.run(
        [
            str(REPO / "scripts" / "standalone_entry.py"),
            "--name",
            executable_name,
            "--onefile",
            "--console",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(dist),
            "--workpath",
            str(work),
            "--specpath",
            str(spec),
            "--paths",
            str(REPO / "src"),
            "--add-data",
            f"{REPO / '.version'}{os.pathsep}joplin_importer",
            "--collect-all",
            "aspose.note",
            "--copy-metadata",
            "aspose-note",
        ]
    )

    artifact = dist / artifact_name
    if not artifact.is_file():
        raise RuntimeError(f"PyInstaller did not create {artifact}")
    print(artifact)
    return artifact


if __name__ == "__main__":
    build()
