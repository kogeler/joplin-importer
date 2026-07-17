"""Secret scan over git-tracked files.

Runs as part of the normal suite, so CI fails if a likely token is tracked.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: something that looks like a real Joplin/Graph token embedded in text
LIKELY_TOKEN_RE = re.compile(r"token[\"'\s:=]{1,4}[0-9a-fA-F]{32,}")

_TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".txt", ".xml", ".cfg", ".ini", ".yaml", ".yml"}


def tracked_files() -> list[str]:
    try:
        output = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    return [line for line in output.splitlines() if line]


def commit_candidate_files() -> list[str]:
    """Tracked plus untracked, non-ignored files that a future commit may add."""
    try:
        output = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    return [line for line in output.splitlines() if line]


def test_sensitive_paths_are_not_tracked():
    tracked = set(tracked_files())
    assert "token" not in tracked
    assert not any(p.startswith((".venv/", "artifacts/")) for p in tracked)


@pytest.mark.parametrize(
    "path",
    [
        "token",
        "artifacts/snapshots/example/manifest.json",
        "artifacts/reports/audit/summary.html",
        "artifacts/export-plan.json",
        "artifacts/export-plan.bodies/note.html",
        "artifacts/export-approval.json",
        "artifacts/backup.jex",
    ],
)
def test_private_runtime_paths_are_ignored(path):
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", path],
            cwd=REPO_ROOT,
            check=False,
        )
    except OSError:
        pytest.skip("git not available")
    assert result.returncode == 0, f"private runtime path is not ignored: {path}"


def test_local_token_value_never_appears_in_tracked_files():
    token_path = REPO_ROOT / "token"
    if not token_path.exists():
        pytest.skip("no local token file")
    secret = token_path.read_text(encoding="utf-8").strip()
    assert secret, "token file exists but is empty"
    for rel in commit_candidate_files():
        path = REPO_ROOT / rel
        if path.suffix not in _TEXT_SUFFIXES or not path.exists():
            continue
        assert secret not in path.read_text(encoding="utf-8", errors="ignore"), (
            f"local token value leaked into tracked file {rel}"
        )


def test_no_embedded_token_literals():
    for rel in commit_candidate_files():
        path = REPO_ROOT / rel
        if path.suffix not in _TEXT_SUFFIXES or not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = LIKELY_TOKEN_RE.search(text)
        assert match is None, f"likely token literal in {rel}: {match.group(0)[:24]}..."


def test_local_home_path_never_appears_in_tracked_files():
    """Prevent committing paths that identify the machine or local account."""
    home = str(Path.home())
    needles = {home.casefold(), home.replace("\\", "/").casefold()}
    for rel in commit_candidate_files():
        path = REPO_ROOT / rel
        if path.suffix not in _TEXT_SUFFIXES or not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").casefold()
        assert all(needle not in text for needle in needles), (
            f"local home path leaked into tracked file {rel}"
        )
