"""Secret loading and redaction.

Tokens are accepted from an environment variable or a local ignored file.
They are wrapped in :class:`Secret` so accidental ``str()``/``repr()``/logging
never exposes the value; only :meth:`Secret.reveal` returns it.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

REDACTED = "***REDACTED***"


class SecretError(RuntimeError):
    """Raised when a secret source is missing, empty, or unsafe."""


class Secret:
    """Opaque wrapper around a sensitive string."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise SecretError("secret value is empty")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Secret({REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))


def load_token(
    *,
    token_file: Path | None = None,
    token_env: str | None = None,
    environ: dict[str, str] | None = None,
) -> Secret:
    """Load a token from a file or an environment variable.

    Exactly one source must be provided. File tokens are stripped of
    surrounding whitespace; empty results are rejected. On POSIX a token file
    readable by group/others is rejected with a clear message.
    """
    if (token_file is None) == (token_env is None):
        raise SecretError("provide exactly one of --token-file or --token-env")

    if token_env is not None:
        env = environ if environ is not None else os.environ
        value = env.get(token_env, "").strip()
        if not value:
            raise SecretError(f"environment variable {token_env!r} is unset or empty")
        return Secret(value)

    assert token_file is not None
    if not token_file.exists():
        raise SecretError(f"token file not found: {token_file}")
    _check_permissions(token_file)
    value = token_file.read_text(encoding="utf-8").strip()
    if not value:
        raise SecretError(f"token file is empty: {token_file}")
    return Secret(value)


def _check_permissions(path: Path) -> None:
    if sys.platform == "win32":  # POSIX mode bits are meaningless there
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise SecretError(
            f"token file {path} is readable by group/others (mode {oct(mode)}); "
            f"run: chmod 600 {path}"
        )


def redact_text(text: str, secrets: list[Secret] | None = None) -> str:
    """Remove token values and token query parameters from arbitrary text."""
    import re

    result = re.sub(r"(token=)[^&\s\"']+", r"\1" + REDACTED, text)
    for secret in secrets or []:
        result = result.replace(secret.reveal(), REDACTED)
    return result
