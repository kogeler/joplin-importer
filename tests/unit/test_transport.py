import httpx
import pytest

from joplin_importer.secretstore import REDACTED, Secret
from joplin_importer.transport import (
    HttpTransport,
    MutationBlockedError,
    RetryPolicy,
    TransportError,
    TransportMode,
)


def make_transport(handler, mode=TransportMode.READ_ONLY, token="tok-123", **kwargs):
    return HttpTransport(
        "http://joplin.test:41184",
        mode=mode,
        token=Secret(token),
        httpx_transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
        **kwargs,
    )


def test_read_only_blocks_mutations_before_io():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)

    transport = make_transport(handler)
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        with pytest.raises(MutationBlockedError):
            transport.request(method, "/notes")
    assert calls == []  # nothing reached the network layer
    assert transport.mutating_requests_sent() == []
    assert all((e.error or "").startswith("blocked") for e in transport.ledger)


def test_read_only_allows_get():
    def handler(request):
        return httpx.Response(200, json={"ok": True})

    transport = make_transport(handler)
    response = transport.get("/ping")
    assert response.status_code == 200


def test_write_enabled_allows_post():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "1"})

    transport = make_transport(handler, mode=TransportMode.WRITE_ENABLED)
    transport.request("POST", "/notes", json_body={"title": "x"})
    assert len(seen) == 1
    assert transport.mutating_requests_sent()[0].method == "POST"


def test_token_is_sent_but_not_logged():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    transport = make_transport(handler)
    transport.get("/notes", params={"page": 1})
    assert "token=tok-123" in str(seen[0].url)  # token actually sent
    entry = transport.ledger[0]
    assert "tok-123" not in entry.url  # ...but never recorded
    assert "page=1" in entry.url


def test_retry_on_429_then_success():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200)

    transport = make_transport(handler)
    assert transport.get("/notes").status_code == 200
    assert len(attempts) == 3
    assert transport.ledger[0].attempts == 3


def test_retry_gives_up_after_max_attempts():
    def handler(request):
        return httpx.Response(503)

    transport = make_transport(handler, retry=RetryPolicy(max_attempts=2))
    with pytest.raises(TransportError, match="HTTP 503"):
        transport.get("/notes")


def test_network_error_message_is_redacted():
    def handler(request):
        raise httpx.ConnectError(f"connection refused for {request.url}")

    transport = make_transport(handler, retry=RetryPolicy(max_attempts=2))
    with pytest.raises(TransportError) as exc_info:
        transport.get("/notes")
    message = str(exc_info.value)
    assert "tok-123" not in message
    assert REDACTED in message


def test_absolute_url_keeps_its_query_when_no_params_given():
    # regression: httpx replaces the URL query with `params`; an empty dict
    # must not strip e.g. Graph @odata.nextLink '$skip=' parameters
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200)

    transport = HttpTransport(
        "http://api.test",
        token=Secret("tok"),
        token_in="header",
        httpx_transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    transport.get("http://api.test/items?$skip=25")
    assert "$skip=25" in seen[0]


def test_header_token_sent_and_not_in_url():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    transport = HttpTransport(
        "http://api.test",
        token=Secret("hdr-tok"),
        token_in="header",
        httpx_transport=httpx.MockTransport(handler),
        sleep=lambda _s: None,
    )
    transport.get("/x")
    assert seen[0].headers["Authorization"] == "Bearer hdr-tok"
    assert "hdr-tok" not in str(seen[0].url)


def test_http_4xx_is_returned_not_retried():
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(404)

    transport = make_transport(handler)
    assert transport.get("/notes/xyz").status_code == 404
    assert len(attempts) == 1
