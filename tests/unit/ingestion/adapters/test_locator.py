"""Pure locator classification: the same table's per-branch half lives in the dispatcher test."""

from __future__ import annotations

import pytest

from poe2_rag.ingestion.adapters.locator import LocatorKind, classify_locator

LOCATOR_CASES = [
    pytest.param(
        "https://www.pathofexile.com/forum/view-thread/1",
        LocatorKind.HTTP,
        id="https-url",
    ),
    pytest.param(
        "http://example.invalid/x",
        LocatorKind.HTTP,
        id="http-url",
    ),
    pytest.param(
        "ftp://x",
        LocatorKind.OTHER_SCHEME,
        id="other-scheme",
    ),
    pytest.param(
        "https:/x",
        LocatorKind.HTTP,
        id="scheme-without-netloc-is-still-http",
    ),
    pytest.param(
        "data/raw/patch-notes-0.1.1.html",
        LocatorKind.PATH,
        id="relative-path",
    ),
    pytest.param(
        "/opt/snapshots/x.html",
        LocatorKind.PATH,
        id="absolute-path",
    ),
]


@pytest.mark.parametrize(("locator", "expected_kind"), LOCATOR_CASES)
def test_classify_locator_returns_the_expected_kind(
    locator: str, expected_kind: LocatorKind
) -> None:
    assert classify_locator(locator) is expected_kind
