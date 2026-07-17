"""Opt-in live smoke test against a local Joplin instance.

Runs ONLY on the Windows test machine, never in CI or on the dev machine:

    .venv\\Scripts\\python.exe -m pytest -m live_joplin tests/integration/test_live_smoke.py

Requirements: Joplin running with the Web Clipper service enabled at
http://127.0.0.1:41184 and the API token stored in the git-ignored `./token`
file. The test is strictly read-only; it fails if any mutating request is
attempted or if the instance fingerprint changes because of its own activity.
External edits or sync during the test are reported as inconclusive.
"""

from pathlib import Path

import pytest

from joplin_importer.adapters.joplin.client import JoplinClient
from joplin_importer.repair.executor import compute_instance_fingerprint
from joplin_importer.secretstore import SecretError, load_token
from joplin_importer.transport import HttpTransport, TransportMode

TOKEN_FILE = Path("token")
BASE_URL = "http://127.0.0.1:41184"

pytestmark = pytest.mark.live_joplin


@pytest.fixture(scope="module")
def client():
    if not TOKEN_FILE.exists():
        pytest.skip("no ./token file; run on the Windows test machine")
    try:
        token = load_token(token_file=TOKEN_FILE)
    except SecretError as exc:
        pytest.skip(f"token unusable: {exc}")
    transport = HttpTransport(BASE_URL, mode=TransportMode.READ_ONLY, token=token)
    client = JoplinClient(transport)
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Joplin Data API not reachable: {exc}")
    return client


def test_live_read_only_inventory(client, tmp_path):
    fingerprint_before = compute_instance_fingerprint(client)

    folders = list(client.iter_folders())
    assert folders, "expected at least one notebook"
    notes_seen = 0
    for _note in client.iter_notes():
        notes_seen += 1
        if notes_seen >= 25:  # smoke test: sample, do not crawl everything
            break

    fingerprint_after = compute_instance_fingerprint(client)
    if fingerprint_before != fingerprint_after:
        pytest.fail(
            "instance fingerprint changed during a read-only scan: environment is "
            "stale (external edits or sync in progress); result inconclusive"
        )

    # the ledger must prove that not a single mutating request was attempted
    assert client.transport.mutating_requests_sent() == []
    assert all(entry.method == "GET" for entry in client.transport.ledger)


def test_live_capability_probe_is_read_only(client):
    capabilities = client.probe_capabilities()
    assert isinstance(capabilities, dict)
    assert client.transport.mutating_requests_sent() == []
