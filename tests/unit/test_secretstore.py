import sys

import pytest

from joplin_importer.secretstore import (
    REDACTED,
    Secret,
    SecretError,
    load_token,
    redact_text,
)


def test_secret_never_prints_value():
    secret = Secret("s3cr3t")
    assert "s3cr3t" not in str(secret)
    assert "s3cr3t" not in repr(secret)
    assert secret.reveal() == "s3cr3t"


def test_empty_secret_rejected():
    with pytest.raises(SecretError):
        Secret("")


def test_load_from_env():
    secret = load_token(token_env="MY_TOKEN", environ={"MY_TOKEN": "  abc  "})
    assert secret.reveal() == "abc"


def test_load_from_missing_env():
    with pytest.raises(SecretError, match="MY_TOKEN"):
        load_token(token_env="MY_TOKEN", environ={})


def test_exactly_one_source_required(tmp_path):
    with pytest.raises(SecretError):
        load_token()
    with pytest.raises(SecretError):
        load_token(token_file=tmp_path / "t", token_env="X")


def test_load_from_file_strips_whitespace(tmp_path):
    path = tmp_path / "token"
    path.write_text("  abc\n")
    path.chmod(0o600)
    assert load_token(token_file=path).reveal() == "abc"


def test_empty_file_rejected(tmp_path):
    path = tmp_path / "token"
    path.write_text("   \n")
    path.chmod(0o600)
    with pytest.raises(SecretError, match="empty"):
        load_token(token_file=path)


def test_missing_file_rejected(tmp_path):
    with pytest.raises(SecretError, match="not found"):
        load_token(token_file=tmp_path / "nope")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission check")
def test_world_readable_file_rejected(tmp_path):
    path = tmp_path / "token"
    path.write_text("abc")
    path.chmod(0o644)
    with pytest.raises(SecretError, match="chmod 600"):
        load_token(token_file=path)


def test_redact_text_query_param():
    text = "GET http://127.0.0.1:41184/notes?page=2&token=abcdef123 failed"
    redacted = redact_text(text)
    assert "abcdef123" not in redacted
    assert "token=" + REDACTED in redacted
    assert "page=2" in redacted


def test_redact_text_raw_secret():
    secret = Secret("raw-secret-value")
    assert "raw-secret-value" not in redact_text("oops raw-secret-value here", [secret])
