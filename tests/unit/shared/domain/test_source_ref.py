from datetime import date

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


@pytest.mark.parametrize("source_type", ["", "  "])
def test_rejects_blank_type(source_type: str) -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef(source_type, "https://poe2db.tw", CAPTURED)


def test_rejects_non_date_captured_at() -> None:
    with pytest.raises(InvalidSourceRefError):
        SourceRef("poe2db", "https://poe2db.tw", "2026-10-29")  # type: ignore[arg-type]


def test_error_is_a_domain_error() -> None:
    assert issubclass(InvalidSourceRefError, DomainError)
