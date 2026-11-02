"""Live fetch of one poe2db gem page (S14).

Marked `network`: it performs a real HTTP request, so the default suite excludes it. Run it at most
once manually with a real contact and report the result; never in CI:

    POE2_RAG_HTTP_CONTACT="<your contact>" \\
        uv run pytest -q -m network tests/network --basetemp=/tmp/poe2db-capture

The page it downloads is a full scraped page: it stays under `tmp_path` and is never committed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx2
import pytest

from poe2_rag.config import HTTP_CONTACT_VARIABLE
from poe2_rag.ingestion.adapters.outbound.http import PoliteHttpFetcher

pytestmark = pytest.mark.network

URL = "https://poe2db.tw/us/Spark"
CACHE_NAME = "poe2db.tw-us-Spark.html"


def test_one_real_fetch_writes_the_page_under_tmp_path(tmp_path: Path) -> None:
    """Fetch Spark once, honouring robots and the contact, and keep the page inside tmp_path."""
    contact = (os.environ.get(HTTP_CONTACT_VARIABLE) or "").strip()
    if not contact:
        pytest.skip(
            f"{HTTP_CONTACT_VARIABLE} is not set; the live fetch requires a real contact so the "
            f"User-Agent identifies the operator"
        )

    with httpx2.Client(timeout=60.0) as client:
        fetcher = PoliteHttpFetcher(
            client=client,
            cache_dir=tmp_path,
            contact=contact,
            request_interval=1.0,
            timeout=60.0,
        )
        raw = fetcher.fetch(URL)

    assert raw.source_type == "poe2db"
    assert raw.locator == URL
    assert raw.fetched_at.tzinfo is not None
    # The observed page was about 700 KB; the lower bound tolerates ordinary layout changes.
    assert len(raw.content) > 100_000
    assert "gemitem" in raw.content

    cache_directory = tmp_path / "poe2db"
    html_path = cache_directory / CACHE_NAME
    assert html_path.read_text(encoding="utf-8") == raw.content
    meta = json.loads(
        (cache_directory / f"{CACHE_NAME}.meta.json").read_text(encoding="utf-8")
    )
    assert meta["url"] == URL
    assert meta["status"] == 200
    assert meta["fetched_at"] == raw.fetched_at.isoformat()
    assert len(meta["sha256"]) == 64

    print(f"captured {len(raw.content)} characters into {html_path}")
