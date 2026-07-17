from joplin_importer.models.timeutil import (
    epoch_seconds,
    utc_iso_from_epoch_ms,
    utc_iso_from_string,
)


def test_epoch_ms_roundtrip():
    iso = utc_iso_from_epoch_ms(1_700_000_000_000)
    assert iso == "2023-11-14T22:13:20Z"
    assert epoch_seconds(iso) == 1_700_000_000.0


def test_zero_and_none_epoch():
    assert utc_iso_from_epoch_ms(0) is None
    assert utc_iso_from_epoch_ms(None) is None


def test_iso_with_offset_is_converted_to_utc():
    assert utc_iso_from_string("2024-05-01T12:00:00+02:00") == "2024-05-01T10:00:00Z"
    assert utc_iso_from_string("2024-05-01T12:00:00Z") == "2024-05-01T12:00:00Z"


def test_naive_iso_assumed_utc():
    assert utc_iso_from_string("2024-05-01T12:00:00") == "2024-05-01T12:00:00Z"


def test_invalid_strings():
    assert utc_iso_from_string("not a date") is None
    assert utc_iso_from_string("") is None
    assert epoch_seconds("garbage") is None
