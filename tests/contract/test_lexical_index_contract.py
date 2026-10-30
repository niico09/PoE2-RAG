"""Contract every `LexicalIndex` implementation must honour (architecture §8).

The port promises three things a caller relies on: only chunks valid at the target patch come back,
the best match comes first, and every result carries its lexical score. The validity rules are
recorded as data in `VALIDITY_CASES`, so the future Chroma-backed `VectorIndex` contract can apply the
same boundaries to its own `search`.

The in-memory implementation in this module is the contract's executable specification: the BM25
adapter is accepted only when it passes the same scenarios. Both are built through one factory, so a
test that passes for one and fails for the other points at the adapter, not at the test.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from poe2_rag.answering.adapters.outbound.lexical.bm25_index import Bm25Index
from poe2_rag.answering.adapters.outbound.lexical.tokenizer import tokenize
from poe2_rag.answering.application import LexicalIndex
from poe2_rag.answering.domain import RetrievedChunk, StageScores
from poe2_rag.shared.domain import (
    Chunk,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)

BuildIndex = Callable[[Sequence[Chunk]], LexicalIndex]

THREAD_URL = "https://www.pathofexile.com/forum/view-thread/1234567"
CAPTURED_AT = date(2026, 10, 29)
TARGET = PatchVersion.parse("0.2.0")


def make_chunk(
    section: str = "skill-changes",
    text: str = "Spark fires lightning projectiles.",
    validity: Validity | None = None,
) -> Chunk:
    """Build one chunk, with an entity slug derived from its patch so distinct ranges stay distinct."""
    range_ = Validity(PatchVersion.parse("0.1.0")) if validity is None else validity
    parsed = range_.valid_from
    slug = f"{parsed.major}-{parsed.minor}-{parsed.patch}"
    return Chunk(
        entity=EntityRef("patch_notes", slug, f"Patch {parsed}"),
        validity=range_,
        section=section,
        text=text,
        source=SourceRef("patch_notes", THREAD_URL, CAPTURED_AT),
    )


class InMemoryLexicalIndex:
    """Reference implementation of the port: term-overlap scoring over a chunk list.

    It reuses the production tokenizer so the matching rule is the port's, not an accident of one
    adapter, and it deliberately avoids BM25: the contract checks the promises, not a ranking.
    """

    def __init__(self, chunks: Sequence[Chunk] = ()) -> None:
        self._chunks = tuple(chunks)

    def rebuild(self, chunks: Sequence[Chunk]) -> None:
        self._chunks = tuple(chunks)

    def search(self, text: str, version: PatchVersion, limit: int) -> tuple[RetrievedChunk, ...]:
        terms = set(tokenize(text))
        ranked = [
            RetrievedChunk(chunk, StageScores(lexical=float(overlap)))
            for chunk in self._chunks
            if chunk.is_valid_at(version)
            for overlap in [sum(1 for term in tokenize(chunk.text) if term in terms)]
            if overlap > 0
        ]
        ranked.sort(key=lambda item: (-lexical_score(item), item.chunk.chunk_id))
        return tuple(ranked[:limit])


def lexical_score(item: RetrievedChunk) -> float:
    """Return the lexical score a result must carry, or fail the contract loudly."""
    score = item.scores.lexical
    assert score is not None, f"{item.chunk.chunk_id!r} came back without a lexical score"
    return score


@pytest.fixture(params=["in-memory", "bm25"])
def build_index(request: pytest.FixtureRequest, tmp_path: Path) -> BuildIndex:
    """Build either implementation from a corpus, so one suite proves both."""
    if request.param == "in-memory":
        return InMemoryLexicalIndex

    directory = tmp_path / "data" / "index"

    def build(chunks: Sequence[Chunk]) -> LexicalIndex:
        index = Bm25Index(directory)
        index.rebuild(chunks)
        return index

    return build


@dataclass(frozen=True)
class ValidityCase:
    """One reusable boundary of the validity filter: a range, a target patch, and the verdict."""

    name: str
    validity: Validity
    target: str
    included: bool


VALIDITY_CASES = (
    ValidityCase(
        "valid_from_is_included", Validity(PatchVersion.parse("0.2.0")), "0.2.0", included=True
    ),
    ValidityCase(
        "valid_until_is_excluded",
        Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")),
        "0.2.0",
        included=False,
    ),
    ValidityCase(
        "open_valid_until_stays_valid", Validity(PatchVersion.parse("0.1.0")), "0.9.0", included=True
    ),
    ValidityCase(
        "before_valid_from_is_excluded",
        Validity(PatchVersion.parse("0.2.0")),
        "0.1.0",
        included=False,
    ),
    ValidityCase(
        "inside_a_closed_range_is_included",
        Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.3.0")),
        "0.2.0",
        included=True,
    ),
)


def chunk_ids(results: Sequence[RetrievedChunk]) -> list[str]:
    """Return the ids of one result, in the order the index returned them."""
    return [item.chunk.chunk_id for item in results]


class TestPortSurface:
    def test_builds_a_lexical_index(self, build_index: BuildIndex) -> None:
        assert isinstance(build_index((make_chunk(),)), LexicalIndex)


class TestValidityFilter:
    @pytest.mark.parametrize("case", VALIDITY_CASES, ids=[case.name for case in VALIDITY_CASES])
    def test_returns_exactly_the_chunks_valid_at_the_target_patch(
        self, build_index: BuildIndex, case: ValidityCase
    ) -> None:
        chunk = make_chunk(validity=case.validity)

        results = build_index((chunk,)).search("spark", PatchVersion.parse(case.target), 10)

        assert chunk_ids(results) == ([chunk.chunk_id] if case.included else [])

    def test_a_stale_chunk_never_appears_even_when_it_matches_best(
        self, build_index: BuildIndex
    ) -> None:
        stale = make_chunk(
            "stale",
            text="Spark spark spark spark damage.",
            validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")),
        )
        current = make_chunk(
            "current",
            text="Spark damage.",
            validity=Validity(PatchVersion.parse("0.2.0")),
        )

        results = build_index((stale, current)).search("spark", TARGET, 10)

        assert chunk_ids(results) == [current.chunk_id]


class TestLimit:
    def test_returns_at_most_the_limit(self, build_index: BuildIndex) -> None:
        chunks = tuple(
            make_chunk(f"section-{index}", text="Spark fires damage.") for index in range(3)
        )

        results = build_index(chunks).search("spark", TARGET, 2)

        assert len(results) == 2

    def test_filters_before_it_applies_the_limit(self, build_index: BuildIndex) -> None:
        """A stale chunk must not consume a slot the caller asked for and could use (D1)."""
        stale = make_chunk(
            "stale",
            text="Spark spark spark damage.",
            validity=Validity(PatchVersion.parse("0.1.0"), PatchVersion.parse("0.2.0")),
        )
        current = make_chunk(
            "current",
            text="Spark damage.",
            validity=Validity(PatchVersion.parse("0.2.0")),
        )

        results = build_index((stale, current)).search("spark", TARGET, 1)

        assert chunk_ids(results) == [current.chunk_id]


class TestScoring:
    def test_every_result_carries_only_its_lexical_score(self, build_index: BuildIndex) -> None:
        chunk = make_chunk()

        (result,) = build_index((chunk,)).search("spark", TARGET, 1)

        assert result.scores.lexical is not None
        assert result.scores.lexical > 0
        assert result.scores.dense is None
        assert result.scores.fused is None
        assert result.scores.rerank is None

    def test_orders_results_best_first(self, build_index: BuildIndex) -> None:
        chunks = (
            make_chunk("alpha", text="Spark spark fires lightning."),
            make_chunk("beta", text="Spark fires damage."),
            make_chunk("gamma", text="Lightning damage only."),
        )

        results = build_index(chunks).search("spark lightning", TARGET, 10)
        scores = [lexical_score(item) for item in results]

        assert scores == sorted(scores, reverse=True)

    def test_returns_each_chunk_once(self, build_index: BuildIndex) -> None:
        chunks = (make_chunk("alpha", text="Spark fires lightning."), make_chunk("beta"))

        ids = chunk_ids(build_index(chunks).search("spark fires lightning", TARGET, 10))

        assert len(ids) == len(set(ids))

    def test_a_query_with_no_matching_term_returns_nothing(self, build_index: BuildIndex) -> None:
        index = build_index((make_chunk(),))

        assert index.search("zebra", TARGET, 10) == ()

    def test_the_same_query_returns_the_same_order_twice(self, build_index: BuildIndex) -> None:
        chunks = (
            make_chunk("alpha", text="Spark fires lightning."),
            make_chunk("beta", text="Spark fires damage."),
        )
        index = build_index(chunks)

        assert chunk_ids(index.search("spark", TARGET, 10)) == chunk_ids(
            index.search("spark", TARGET, 10)
        )
