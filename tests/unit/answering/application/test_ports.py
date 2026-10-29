"""The query ports and the raw model answer they exchange."""

from dataclasses import FrozenInstanceError

import pytest

from poe2_rag.answering.application import (
    AnswerQuestionError,
    ChunkMismatchError,
    DuplicateChunkError,
    GeneratedAnswer,
    InvalidGeneratedAnswerError,
    InvalidRetrievalSettingsError,
    LexicalIndex,
    MissingScoreError,
    StaleChunkError,
    UnretrievedChunkError,
)
from poe2_rag.answering.domain import Citation
from poe2_rag.shared.domain import DomainError

CHUNK_ID = "poe2db:active_gem:spark@0.1.2#description"


def citation() -> Citation:
    return Citation(CHUNK_ID, "12 mana")


def test_application_errors_are_not_domain_errors() -> None:
    """A broken outbound contract is an application failure, not a domain invariant violation."""
    assert issubclass(AnswerQuestionError, ValueError)
    assert not issubclass(AnswerQuestionError, DomainError)
    for error in (
        InvalidGeneratedAnswerError,
        InvalidRetrievalSettingsError,
        StaleChunkError,
        UnretrievedChunkError,
        DuplicateChunkError,
        MissingScoreError,
        ChunkMismatchError,
    ):
        assert issubclass(error, AnswerQuestionError)


class TestGeneratedAnswer:
    def test_keeps_text_and_citations(self) -> None:
        answer = GeneratedAnswer("Spark costs 12 mana.", (citation(),))

        assert answer.text == "Spark costs 12 mana."
        assert answer.citations == (citation(),)

    def test_is_frozen(self) -> None:
        answer = GeneratedAnswer("Spark costs 12 mana.", ())

        with pytest.raises(FrozenInstanceError):
            answer.text = "other"  # type: ignore[misc]

    def test_rejects_blank_text(self) -> None:
        with pytest.raises(InvalidGeneratedAnswerError, match="text"):
            GeneratedAnswer("   ", ())

    def test_rejects_a_non_string_text(self) -> None:
        with pytest.raises(InvalidGeneratedAnswerError, match="text"):
            GeneratedAnswer(None, ())  # type: ignore[arg-type]

    def test_rejects_citations_that_are_not_a_tuple(self) -> None:
        with pytest.raises(InvalidGeneratedAnswerError, match="citations"):
            GeneratedAnswer("Spark costs 12 mana.", [citation()])  # type: ignore[arg-type]

    def test_rejects_items_that_are_not_citations(self) -> None:
        with pytest.raises(InvalidGeneratedAnswerError, match="citations"):
            GeneratedAnswer("Spark costs 12 mana.", (CHUNK_ID,))  # type: ignore[arg-type]

    def test_allows_no_citations(self) -> None:
        """The model may refuse to answer; `Answer.build` turns that into an unsupported answer."""
        assert GeneratedAnswer("No information available.", ()).citations == ()


class TestPorts:
    def test_an_object_with_the_method_satisfies_its_port(self) -> None:
        """Ports are `runtime_checkable`, so contract tests can assert an adapter implements one."""

        class Index:
            def search(self, text, version, limit):
                return ()

        assert isinstance(Index(), LexicalIndex)
        assert not isinstance(object(), LexicalIndex)
