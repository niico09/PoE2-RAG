import re
from datetime import date

import pytest

from poe2_rag.answering.domain import (
    Answer,
    Citation,
    InvalidAnswerError,
    InvalidCitationError,
    InvalidRetrievedChunkError,
    RetrievedChunk,
    StageScores,
)
from poe2_rag.shared.domain import (
    Chunk,
    DomainError,
    EntityRef,
    PatchVersion,
    SourceRef,
    Validity,
)


def make_chunk(slug: str = "spark", section: str = "description", text: str | None = None) -> Chunk:
    return Chunk(
        entity=EntityRef("active_gem", slug, slug.title()),
        validity=Validity(PatchVersion.parse("0.1.2")),
        section=section,
        text=text or "Spark fires lightning projectiles that bounce off walls.",
        source=SourceRef("poe2db", f"https://poe2db.tw/us/{slug.title()}", date(2026, 10, 29)),
    )


def delivered(*chunks: Chunk) -> list[RetrievedChunk]:
    return [RetrievedChunk(chunk, StageScores()) for chunk in chunks]


def test_errors_are_domain_errors() -> None:
    assert issubclass(InvalidCitationError, DomainError)
    assert issubclass(InvalidAnswerError, DomainError)


class TestCitation:
    def test_keeps_chunk_id_and_quoted_text(self) -> None:
        citation = Citation("active_gem:spark@0.1.2#description", "12 mana")

        assert citation.chunk_id == "active_gem:spark@0.1.2#description"
        assert citation.quoted_text == "12 mana"

    def test_rejects_a_blank_chunk_id(self) -> None:
        with pytest.raises(InvalidCitationError, match="chunk_id"):
            Citation("  ", "12 mana")

    def test_rejects_a_non_string_chunk_id(self) -> None:
        with pytest.raises(InvalidCitationError, match="chunk_id"):
            Citation(None, "12 mana")  # type: ignore[arg-type]

    def test_rejects_a_blank_quoted_text(self) -> None:
        with pytest.raises(InvalidCitationError, match="quoted_text"):
            Citation("active_gem:spark@0.1.2#description", "   ")

    def test_rejects_a_non_string_quoted_text(self) -> None:
        with pytest.raises(InvalidCitationError, match="quoted_text"):
            Citation("active_gem:spark@0.1.2#description", 12)  # type: ignore[arg-type]


class TestAnswerConstruction:
    def test_keeps_text_citations_and_support(self) -> None:
        citation = Citation("active_gem:spark@0.1.2#description", "12 mana")

        answer = Answer("Spark costs 12 mana.", (citation,), True)

        assert answer.text == "Spark costs 12 mana."
        assert answer.citations == (citation,)
        assert answer.supported is True

    def test_rejects_blank_text(self) -> None:
        with pytest.raises(InvalidAnswerError, match="text"):
            Answer("   ", (), False)

    def test_rejects_citations_that_are_not_a_tuple(self) -> None:
        citation = Citation("active_gem:spark@0.1.2#description", "12 mana")

        with pytest.raises(InvalidCitationError, match="citations"):
            Answer("Spark costs 12 mana.", [citation], True)  # type: ignore[arg-type]

    def test_rejects_citations_that_are_not_citations(self) -> None:
        with pytest.raises(InvalidCitationError, match="citations"):
            Answer("Spark costs 12 mana.", ("active_gem:spark@0.1.2#description",), True)  # type: ignore[arg-type]

    def test_rejects_a_non_bool_support_flag(self) -> None:
        with pytest.raises(InvalidAnswerError, match="supported"):
            Answer("Spark costs 12 mana.", (), "no")  # type: ignore[arg-type]

    def test_an_answer_without_citations_cannot_claim_support(self) -> None:
        with pytest.raises(InvalidAnswerError, match="backing"):
            Answer("Spark costs 12 mana.", (), True)

    def test_an_answer_without_citations_is_valid_as_unsupported(self) -> None:
        answer = Answer("No information available for 0.1.2.", (), False)

        assert answer.supported is False

    def test_an_answer_with_citations_may_still_be_marked_unsupported(self) -> None:
        citation = Citation("active_gem:spark@0.1.2#description", "12 mana")

        assert Answer("Spark costs 12 mana.", (citation,), False).supported is False


@pytest.mark.parametrize("citations", ["no-es-tupla", None, 42])
def test_both_paths_reject_a_malformed_citations_argument_the_same_way(citations: object) -> None:
    """A caller catching InvalidCitationError must see it from either construction path."""
    with pytest.raises(InvalidCitationError):
        Answer("Spark costs 12 mana.", citations, False)  # type: ignore[arg-type]
    with pytest.raises(InvalidCitationError):
        Answer.build("Spark costs 12 mana.", citations, [])  # type: ignore[arg-type]


class TestAnswerBuild:
    def test_builds_a_supported_answer_when_every_citation_was_delivered(self) -> None:
        chunk = make_chunk()
        citation = Citation(chunk.chunk_id, "lightning projectiles")

        answer = Answer.build("Spark fires projectiles.", [citation], delivered(chunk))

        assert answer.supported is True
        assert answer.citations == (citation,)
        assert answer.text == "Spark fires projectiles."

    def test_rejects_a_citation_to_a_chunk_that_was_not_delivered(self) -> None:
        chunk = make_chunk()
        missing = make_chunk(slug="blight")

        with pytest.raises(InvalidCitationError, match=re.escape(missing.chunk_id)):
            Answer.build("Blight spreads.", [Citation(missing.chunk_id, "spread")], delivered(chunk))

    def test_rejects_when_only_one_of_several_citations_was_not_delivered(self) -> None:
        chunk = make_chunk()
        missing = make_chunk(slug="blight")
        citations = [Citation(chunk.chunk_id, "lightning"), Citation(missing.chunk_id, "spread")]

        with pytest.raises(InvalidCitationError, match="blight"):
            Answer.build("Both.", citations, delivered(chunk))

    def test_rejects_every_citation_when_nothing_was_delivered(self) -> None:
        with pytest.raises(InvalidCitationError, match="not delivered"):
            Answer.build("Spark costs 12 mana.", [Citation(make_chunk().chunk_id, "12 mana")], [])

    def test_no_citations_builds_an_unsupported_answer(self) -> None:
        answer = Answer.build("No information available for 0.1.2.", [], delivered(make_chunk()))

        assert answer.citations == ()
        assert answer.supported is False

    def test_an_unsupported_answer_without_citations_is_never_silently_supported(self) -> None:
        answer = Answer.build("No information available.", [], [])

        assert answer.supported is False

    def test_normalizes_a_list_of_citations_to_a_tuple(self) -> None:
        chunk = make_chunk()

        answer = Answer.build("Spark.", [Citation(chunk.chunk_id, "lightning")], delivered(chunk))

        assert isinstance(answer.citations, tuple)

    def test_validates_citations_by_chunk_id_not_by_object_identity(self) -> None:
        original = make_chunk()
        same_id = make_chunk(text="A different text that shares the same chunk id.")

        answer = Answer.build("Spark.", [Citation(same_id.chunk_id, "lightning")], delivered(original))

        assert answer.supported is True

    def test_accepts_the_same_chunk_delivered_twice(self) -> None:
        chunk = make_chunk()

        answer = Answer.build("Spark.", [Citation(chunk.chunk_id, "lightning")], delivered(chunk, chunk))

        assert answer.supported is True

    def test_rejects_citations_that_are_not_citations(self) -> None:
        with pytest.raises(InvalidCitationError, match="citations"):
            Answer.build("Spark.", [make_chunk().chunk_id], delivered(make_chunk()))  # type: ignore[list-item]

    def test_rejects_delivered_items_that_are_not_retrieved_chunks(self) -> None:
        with pytest.raises(InvalidRetrievedChunkError, match="delivered"):
            Answer.build("Spark.", [], [make_chunk()])  # type: ignore[list-item]

    def test_rejects_a_bare_string_as_delivered(self) -> None:
        with pytest.raises(InvalidRetrievedChunkError, match="delivered"):
            Answer.build("Spark.", [], "active_gem:spark@0.1.2#description")  # type: ignore[arg-type]

    def test_validates_the_answer_text(self) -> None:
        with pytest.raises(InvalidAnswerError, match="text"):
            Answer.build("  ", [], delivered(make_chunk()))
