"""The local BGE embedder, without the model: query and document sides differ by instruction only.

The real model is exercised by `tests/integration/test_dense_retrieval_integration.py`. Here the loader
is replaced by a stub, so the default suite stays offline and fast while still pinning the rules the
adapter owns: lazy load, the retrieval instruction on queries and never on documents, one vector per
text in order, L2 normalization requested from the model, the overflow report and a fixed dimension.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from poe2_rag.answering.adapters.outbound.dense import bge_embedder
from poe2_rag.answering.adapters.outbound.dense.bge_embedder import (
    DEFAULT_DIMENSION,
    DEFAULT_MAX_SEQUENCE_LENGTH,
    DEFAULT_MODEL_ID,
    QUERY_INSTRUCTION,
    BgeEmbedder,
    BlankTextError,
    EmbedderConfigurationError,
    EmbedderError,
)
from poe2_rag.answering.application import Embedder
from poe2_rag.ingestion.application import DocumentEmbedder


@dataclass(frozen=True)
class EncodeCall:
    """One `encode` the adapter asked the model for, with the arguments it used."""

    sentences: tuple[str, ...]
    batch_size: int
    normalize_embeddings: bool
    convert_to_numpy: bool
    show_progress_bar: bool


class StubTokenizer:
    """Counts one token per whitespace-separated word, which is all the overflow check reads."""

    def __call__(
        self, texts: list[str], *, truncation: bool, add_special_tokens: bool, padding: bool
    ) -> dict[str, list[list[int]]]:
        return {"input_ids": [[0] * len(text.split()) for text in texts]}


class StubModel:
    """Stands in for `SentenceTransformer`: records every encode call and returns fixed vectors."""

    def __init__(self, dimension: int = DEFAULT_DIMENSION) -> None:
        self.calls: list[EncodeCall] = []
        self.tokenizer = StubTokenizer()
        self.max_seq_length = DEFAULT_MAX_SEQUENCE_LENGTH
        self._dimension = dimension

    def encode(
        self,
        sentences: list[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
        convert_to_numpy: bool,
        show_progress_bar: bool,
    ) -> list[list[float]]:
        self.calls.append(
            EncodeCall(
                tuple(sentences),
                batch_size,
                normalize_embeddings,
                convert_to_numpy,
                show_progress_bar,
            )
        )
        return [[float(index + 1)] * self._dimension for index in range(len(sentences))]

    def get_embedding_dimension(self) -> int:
        return self._dimension


class Loader:
    """Records every model load and hands out one stub model."""

    def __init__(self, model: StubModel | None = None) -> None:
        self.model = model if model is not None else StubModel()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model_id: str, device: str) -> StubModel:
        self.calls.append((model_id, device))
        return self.model


@pytest.fixture
def loader(monkeypatch: pytest.MonkeyPatch) -> Loader:
    """Replace the real loader, so no test in this file downloads or imports the model."""
    installed = Loader()
    monkeypatch.setattr(bge_embedder, "_load_model", installed)
    return installed


class TestIdentity:
    def test_defaults_to_bge_base_en_v1_5(self) -> None:
        embedder = BgeEmbedder()

        assert embedder.model_id == "BAAI/bge-base-en-v1.5"
        assert DEFAULT_MODEL_ID == "BAAI/bge-base-en-v1.5"
        assert embedder.dimension == 768
        assert DEFAULT_DIMENSION == 768

    def test_takes_the_model_and_dimension_from_the_caller(self) -> None:
        embedder = BgeEmbedder("BAAI/bge-small-en-v1.5", dimension=384)

        assert embedder.model_id == "BAAI/bge-small-en-v1.5"
        assert embedder.dimension == 384

    def test_reading_the_identity_does_not_load_the_model(self, loader: Loader) -> None:
        embedder = BgeEmbedder()

        assert embedder.model_id and embedder.dimension
        assert loader.calls == []

    def test_satisfies_both_embedder_ports(self) -> None:
        assert isinstance(BgeEmbedder(), Embedder)
        assert isinstance(BgeEmbedder(), DocumentEmbedder)


class TestQuerySide:
    def test_prefixes_the_retrieval_instruction_to_a_query(self, loader: Loader) -> None:
        embedder = BgeEmbedder()

        vector = embedder.embed("what does spark do")

        (call,) = loader.model.calls
        assert call.sentences == (f"{QUERY_INSTRUCTION}what does spark do",)
        assert len(vector) == DEFAULT_DIMENSION

    def test_returns_a_tuple_of_floats(self, loader: Loader) -> None:
        vector = BgeEmbedder().embed("spark")

        assert isinstance(vector, tuple)
        assert all(isinstance(value, float) for value in vector)

    @pytest.mark.parametrize("text", ["", "   ", None, 1])
    def test_rejects_a_blank_or_non_string_query(self, loader: Loader, text: object) -> None:
        with pytest.raises(BlankTextError):
            BgeEmbedder().embed(text)  # type: ignore[arg-type]

    def test_rejects_a_blank_query_before_it_loads_the_model(self, loader: Loader) -> None:
        with pytest.raises(BlankTextError):
            BgeEmbedder().embed("")

        assert loader.calls == []


class TestDocumentSide:
    def test_embeds_documents_without_the_query_instruction(self, loader: Loader) -> None:
        """The instruction is a query-side transformation; documents are embedded as they are."""
        vectors = BgeEmbedder().embed_documents(("spark", "spark fires lightning"))

        (call,) = loader.model.calls
        assert call.sentences == ("spark", "spark fires lightning")
        assert len(vectors) == 2

    def test_returns_one_vector_per_text_in_order(self, loader: Loader) -> None:
        vectors = BgeEmbedder().embed_documents(("first", "second"))

        assert vectors[0] == (1.0,) * DEFAULT_DIMENSION
        assert vectors[1] == (2.0,) * DEFAULT_DIMENSION

    def test_asks_the_model_to_normalize_the_vectors(self, loader: Loader) -> None:
        BgeEmbedder(batch_size=8).embed_documents(("spark",))

        (call,) = loader.model.calls
        assert call.normalize_embeddings is True
        assert call.convert_to_numpy is True
        assert call.show_progress_bar is False
        assert call.batch_size == 8

    def test_applies_the_sequence_limit_to_the_loaded_model(self, loader: Loader) -> None:
        """The model truncates at its own window, so the injected limit must reach it."""
        BgeEmbedder(max_sequence_length=64).embed("spark")

        assert loader.model.max_seq_length == 64

    def test_an_empty_batch_returns_no_vector_and_loads_nothing(self, loader: Loader) -> None:
        assert BgeEmbedder().embed_documents(()) == ()
        assert loader.calls == []

    @pytest.mark.parametrize("texts", [[""], ["spark", "  "], ["spark", None]])
    def test_rejects_a_blank_or_non_string_document(
        self, loader: Loader, texts: list[object]
    ) -> None:
        with pytest.raises(BlankTextError):
            BgeEmbedder().embed_documents(texts)  # type: ignore[arg-type]

    def test_loads_the_model_once_across_calls(self, loader: Loader) -> None:
        embedder = BgeEmbedder()

        embedder.embed("spark")
        embedder.embed_documents(("spark",))
        embedder.overflowing_indices(("spark",))

        assert loader.calls == [(DEFAULT_MODEL_ID, "cpu")]


class TestOverflow:
    def test_reports_the_positions_of_texts_above_the_sequence_limit(self, loader: Loader) -> None:
        embedder = BgeEmbedder(max_sequence_length=3)

        overflowing = embedder.overflowing_indices(("one two three", "one two three four", "one"))

        assert overflowing == (1,)

    def test_reports_nothing_when_every_text_fits(self, loader: Loader) -> None:
        assert BgeEmbedder(max_sequence_length=8).overflowing_indices(("one two",)) == ()

    def test_an_empty_batch_reports_nothing(self, loader: Loader) -> None:
        assert BgeEmbedder().overflowing_indices(()) == ()

    def test_does_not_truncate_before_counting(self, loader: Loader) -> None:
        """Counting with truncation on would hide the overflow the report exists for (ADR-0004)."""
        counted: list[bool] = []

        class RecordingTokenizer(StubTokenizer):
            def __call__(self, texts: Any, *, truncation: bool, **kwargs: Any) -> Any:
                counted.append(truncation)
                return super().__call__(texts, truncation=truncation, **kwargs)

        loader.model.tokenizer = RecordingTokenizer()

        BgeEmbedder(max_sequence_length=2).overflowing_indices(("one two three",))

        assert counted == [False]


class TestConfiguration:
    def test_a_model_with_another_dimension_fails_loud(self, loader: Loader) -> None:
        """A model narrower than the configured width must not be embedded into the index."""
        embedder = BgeEmbedder("BAAI/bge-small-en-v1.5", dimension=384)

        with pytest.raises(EmbedderConfigurationError, match="768"):
            embedder.embed("spark")

    def test_rejects_a_non_positive_batch_size(self) -> None:
        with pytest.raises(EmbedderConfigurationError):
            BgeEmbedder(batch_size=0)

    def test_rejects_a_non_positive_sequence_limit(self) -> None:
        with pytest.raises(EmbedderConfigurationError):
            BgeEmbedder(max_sequence_length=0)

    def test_rejects_a_blank_model_id(self) -> None:
        with pytest.raises(EmbedderConfigurationError):
            BgeEmbedder("  ")


class TestErrors:
    def test_embedder_errors_are_value_errors(self) -> None:
        assert issubclass(EmbedderError, ValueError)
        assert issubclass(BlankTextError, EmbedderError)
        assert issubclass(EmbedderConfigurationError, EmbedderError)
