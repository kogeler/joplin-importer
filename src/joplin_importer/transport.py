"""Shared HTTP transport with an enforceable mutation guard.

All Joplin traffic goes through :class:`HttpTransport`. In ``READ_ONLY`` mode
the transport raises :class:`MutationBlockedError` for POST/PUT/PATCH/DELETE
*before* any network I/O — safety is structural, not a convention inside
action handlers. Every request is recorded in a redacted ledger so audits and
the live smoke test can prove that no mutating request was sent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from .secretstore import Secret, redact_text

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class TransportMode(StrEnum):
    READ_ONLY = "read-only"
    WRITE_ENABLED = "write-enabled"


class TransportError(RuntimeError):
    """HTTP failure with a redacted message."""


class MutationBlockedError(TransportError):
    """A mutating request was attempted while the transport is read-only."""


@dataclass
class LedgerEntry:
    method: str
    url: str  # redacted
    status_code: int | None = None
    error: str | None = None  # redacted
    attempts: int = 1


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 30.0
    retry_statuses: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class HttpTransport:
    """httpx-based transport with mutation guard, retries, and redaction."""

    def __init__(
        self,
        base_url: str,
        *,
        mode: TransportMode = TransportMode.READ_ONLY,
        token: Secret | None = None,
        token_param: str = "token",  # noqa: S107 - query parameter name, not a secret
        token_in: str = "query",  # noqa: S107 - "query" (Joplin) or "header" (Graph)
        retry: RetryPolicy | None = None,
        timeout_seconds: float = 60.0,
        sleep=time.sleep,
        httpx_transport: httpx.BaseTransport | None = None,
    ) -> None:
        if token_in not in ("query", "header"):
            raise ValueError("token_in must be 'query' or 'header'")
        self.mode = mode
        self._token = token
        self._token_param = token_param
        self._token_in = token_in
        self._retry = retry or RetryPolicy()
        self._sleep = sleep
        self.ledger: list[LedgerEntry] = []
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            transport=httpx_transport,
            follow_redirects=False,
        )

    # -- public API ---------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        content: bytes | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        method = method.upper()
        redacted_url = self._redacted_url(path, params)
        if method in MUTATING_METHODS and self.mode is not TransportMode.WRITE_ENABLED:
            entry = LedgerEntry(method=method, url=redacted_url, error="blocked: read-only mode")
            self.ledger.append(entry)
            raise MutationBlockedError(
                f"transport is {self.mode}; refusing to send {method} {redacted_url}"
            )

        merged_params = dict(params or {})
        headers: dict[str, str] = {}
        if self._token is not None:
            if self._token_in == "header":  # noqa: S105 - mode check, not a secret
                headers["Authorization"] = f"Bearer {self._token.reveal()}"
            else:
                merged_params[self._token_param] = self._token.reveal()

        entry = LedgerEntry(method=method, url=redacted_url)
        self.ledger.append(entry)
        last_error: Exception | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            entry.attempts = attempt
            try:
                response = self._client.request(
                    method,
                    path,
                    # httpx *replaces* the URL query with params; pass None when
                    # empty so absolute next-link URLs keep their own query
                    params=merged_params or None,
                    headers=headers or None,
                    json=json_body,
                    content=content,
                    files=files,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                entry.error = self._redact(f"{type(exc).__name__}: {exc}")
                if attempt < self._retry.max_attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                raise TransportError(
                    f"request failed after {attempt} attempts: {method} {redacted_url}: "
                    f"{entry.error}"
                ) from None  # drop the original exception: its args may embed the token URL
            entry.status_code = response.status_code
            if response.status_code in self._retry.retry_statuses:
                if attempt < self._retry.max_attempts:
                    self._sleep(self._retry_after(response) or self._backoff(attempt))
                    continue
                entry.error = f"gave up after {attempt} attempts"
                raise TransportError(
                    f"HTTP {response.status_code} after {attempt} attempts: "
                    f"{method} {redacted_url}"
                )
            entry.error = None
            return response
        raise TransportError(  # pragma: no cover - defensive, loop always returns/raises
            f"request failed: {method} {redacted_url}: {self._redact(str(last_error))}"
        )

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self.request("GET", path, params=params)

    def mutating_requests_sent(self) -> list[LedgerEntry]:
        """Ledger entries for mutating requests that were actually attempted."""
        return [
            e
            for e in self.ledger
            if e.method in MUTATING_METHODS and not (e.error or "").startswith("blocked")
        ]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpTransport:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- internals ------------------------------------------------------------

    def _redact(self, text: str) -> str:
        secrets = [self._token] if self._token is not None else []
        return redact_text(text, secrets)

    def _redacted_url(self, path: str, params: dict[str, Any] | None) -> str:
        url = str(self._client.base_url.join(path))
        if params:
            visible = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            url = f"{url}?{visible}"
        return self._redact(url)

    def _backoff(self, attempt: int) -> float:
        delay = self._retry.backoff_base_seconds * (2 ** (attempt - 1))
        return min(delay, self._retry.backoff_max_seconds)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None
