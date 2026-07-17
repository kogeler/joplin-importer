from joplin_importer.models.hashing import (
    canonical_json,
    sha256_bytes,
    sha256_canonical_json,
    sha256_text,
)


def test_canonical_json_is_order_independent():
    a = {"b": 1, "a": [1, 2], "c": {"y": None, "x": "ü"}}
    b = {"c": {"x": "ü", "y": None}, "a": [1, 2], "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert sha256_canonical_json(a) == sha256_canonical_json(b)


def test_canonical_json_keeps_unicode_readable():
    assert canonical_json({"t": "тест"}) == '{"t":"тест"}'


def test_sha256_text_matches_bytes():
    assert sha256_text("abc") == sha256_bytes(b"abc")
    # well-known vector
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_list_order_is_significant():
    assert sha256_canonical_json([1, 2]) != sha256_canonical_json([2, 1])
