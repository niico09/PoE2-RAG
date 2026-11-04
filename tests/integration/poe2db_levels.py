"""A 40-row poe2db gem page for the level-block suites.

The committed `active_gem_spark.html` is a two-row excerpt of the captured page and a full capture may
not be committed (ADR-0002), so the 40-row, nine-column shape the capture showed is rebuilt here: the
table header comes from the fixture itself and the rows are generated wide enough to match the widest
row measured on the six real captures (72 characters), never narrower. Both integration suites parse
this page, so it lives in one module instead of two copies that could drift.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx2
from cli_e2e import ROBOTS_URL

from poe2_rag.ingestion.adapters.outbound.poe2db import (
    DEFAULT_LEVEL_BLOCK_SIZE,
    Poe2dbGemPageParser,
)
from poe2_rag.ingestion.application import RawDocument
from poe2_rag.shared.domain import (
    Chunk,
    InMemoryPatchCatalog,
    PatchRelease,
    PatchVersion,
)

URL = "https://poe2db.tw/us/Spark"
FIXTURE = Path(__file__).parents[1] / "fixtures" / "poe2db" / "active_gem_spark.html"
CAPTURED_AT = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)
PATCH = PatchVersion.parse("0.1.1")
ROW_COUNT = 40
OBSERVED_WIDEST_ROW = 72
"""Characters in the widest row of the six captures, measured on the Ice Nova table."""
BLOCKS = ["levels-01-10", "levels-11-20", "levels-21-30", "levels-31-40"]

CATALOG = InMemoryPatchCatalog([PatchRelease(PATCH, date(2025, 1, 17))], PATCH)

PAGE = """
<div class="newItemPopup GemPopup">
  <div class="itemHeader">
    <div class="itemName"><span class="lc">Spark</span></div>
    <div class="itemName typeLine"><span class="lc">Spell</span></div>
  </div>
  <div class="content">
    <div class="Stats">
      <div class="property"><a class="GemTags">Projectile</a></div>
      <div class="secDescrText">Launch a spray of sparking Projectiles.</div>
    </div>
    {table}
  </div>
</div>
"""


def level_rows(count: int = ROW_COUNT) -> list[tuple[str, ...]]:
    """Return `count` rows with Spark's nine columns, widening from level 1 to `count`."""
    rows: list[tuple[str, ...]] = []
    for level in range(1, count + 1):
        step = (level - 1) / max(count - 1, 1)
        rows.append(
            (
                str(level),
                str(round(step * 90)),
                str(round(4 + step * 153)),
                str(round(5 + step * 51)),
                "100%, 100%, 100%, 100%",
                f"{round(1 + step * 12)}, {round(10 + step * 235)}",
                str(4 + int(step * 5)),
                f"{round(7 + step * 200)}, {round(11 + step * 299)}",
                str(12 + int(step * 4)),
            )
        )
    return rows


def rendered_rows(count: int = ROW_COUNT) -> list[str]:
    """Return the row lines the parser must reproduce, exactly as the table renders them."""
    return [" | ".join(row) for row in level_rows(count)]


def parse_page(
    content: str, level_block_size: int = DEFAULT_LEVEL_BLOCK_SIZE
) -> tuple[Chunk, ...]:
    """Parse one page with the real parser at the given block size, the default unless one is given."""
    parser = Poe2dbGemPageParser(CATALOG, level_block_size=level_block_size)
    return parser.parse(
        RawDocument(locator=URL, content=content, fetched_at=CAPTURED_AT, source_type="poe2db")
    )


def level_blocks(chunks: tuple[Chunk, ...]) -> list[Chunk]:
    """Return the level blocks of a parsed page, in document order."""
    return [chunk for chunk in chunks if chunk.section.startswith("levels-")]


def fixture_header() -> list[str]:
    """Return the level-table header the committed fixture renders, cell by cell."""
    (block,) = level_blocks(parse_page(FIXTURE.read_text(encoding="utf-8")))
    return block.text.splitlines()[1].split(" | ")


def spark_level_page(count: int = ROW_COUNT) -> str:
    """Return a gem page whose level table holds `count` generated rows under the fixture's header."""
    header = "".join(f"<th>{cell}</th>" for cell in fixture_header())
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in level_rows(count)
    )
    return PAGE.format(table=f"<table><tr>{header}</tr>{rows}</table>")


def client_for(page: str) -> httpx2.Client:
    """Serve robots.txt and `page` for `URL`, so an ingest run reaches the real fetcher offline."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == ROBOTS_URL:
            return httpx2.Response(200, text="User-agent: *\nAllow: /")
        if url != URL:
            raise AssertionError(f"unexpected request to {url}")
        return httpx2.Response(200, text=page)

    return httpx2.Client(transport=httpx2.MockTransport(handler))
