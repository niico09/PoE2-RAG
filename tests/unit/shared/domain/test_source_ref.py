from datetime import date, datetime

import pytest

from poe2_rag.shared.domain import DomainError, InvalidSourceRefError, SourceRef

CAPTURED = date(2026, 10, 29)


@pytest.mark.parametrize("url", ["https://poe2db.tw/us/Spark", "http://example.com"])
def test_accepts_http_and_https_urls(url: str) -> None:
    assert SourceRef("poe2db", url, CAPTURED).url == url


@pytest.mark.parametrize("url", ["", "poe2db.tw/us/Spark", "ftp://example.com", "https://", "javascript:alert(1)"])
def test_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", url, CAPTURED)


def test_rejects_unparseable_url() -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", "http://[::1", CAPTURED)


@pytest.mark.parametrize("url", ["http://[::1]/x", "https://localhost:8090/x"])
def test_accepts_valid_hosts(url: str) -> None:
    assert SourceRef("poe2db", url, CAPTURED).url == url


@pytest.mark.parametrize(
    "url", ["https://exa mple.com", "https://.a", "https://@:80", "https://exa\u00a0mple.com"]
)
def test_rejects_malformed_hosts(url: str) -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", url, CAPTURED)


@pytest.mark.parametrize("url", [123, None, b"https://poe2db.tw"])
def test_rejects_a_non_string_url_with_a_domain_error(url: object) -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", url, CAPTURED)  # type: ignore[arg-type]


def test_strips_leading_and_trailing_url_whitespace() -> None:
    ref = SourceRef("poe2db", "  https://poe2db.tw/us/Spark  ", CAPTURED)

    assert ref.url == "https://poe2db.tw/us/Spark"


def test_padded_url_equals_unpadded_url() -> None:
    assert SourceRef("poe2db", "  https://poe2db.tw/us/Spark", CAPTURED) == SourceRef(
        "poe2db", "https://poe2db.tw/us/Spark", CAPTURED
    )


@pytest.mark.parametrize("source_type", ["", "  "])
def test_rejects_blank_type(source_type: str) -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef(source_type, "https://poe2db.tw", CAPTURED)


def test_rejects_non_date_captured_at() -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", "https://poe2db.tw", "2026-10-29")  # type: ignore[arg-type]


def test_rejects_datetime_captured_at() -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", "https://poe2db.tw", datetime(2026, 10, 29, 12, 0))  # type: ignore[arg-type]


def test_accepts_date_captured_at() -> None:
    assert SourceRef("poe2db", "https://poe2db.tw", CAPTURED).captured_at == CAPTURED


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidSourceRefError, DomainError)
